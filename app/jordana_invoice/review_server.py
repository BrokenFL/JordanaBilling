from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import secrets
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .build_info import current_build_info
from .db import DatabaseBusyError, MigrationError, connect, migrate_database
from .google_sync import (
    default_transport,
    load_sync_config_for_database,
    next_sync_time_iso,
    public_sync_status,
    rebuild_calendar_data_from_sheet,
    sanitize_sync_error,
    SyncError,
    sync_calendar_automatically,
    sync_interval_minutes_from_env,
    sync_status_for_connection,
    sync_with_process_lock,
)
from .review_services import (
    add_account_member,
    archive_already_classified_personal_admin,
    archive_person,
    analyze_billing_relationship_duplicates,
    approve_candidate,
    BillingPartyNotFoundError,
    BillingPartyTypeError,
    create_account,
    create_account_or_return_existing,
    deactivate_account,
    delete_or_archive_billing_relationship,
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
    normalize_duplicate_payer_billing_parties,
    promote_candidate_to_review,
    recalc_unapproved_session_rates,
    reparse_unapproved_candidates,
    restore_candidate,
    return_approved_session_to_review,
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
    set_sessions_archive_state,
    search_accounts,
    search_billing_parties,
    search_organization_billing_parties,
    search_people,
    setup_billing_relationship,
    update_account,
    update_billing_party,
    update_billing_relationship,
    update_person,
    preview_copy_contact_details,
    apply_copy_contact_details,
)
from .invoice_services import (
    add_sessions_to_draft,
    create_invoice_draft,
    start_invoice_correction,
    delete_invoice_draft,
    eligible_sessions,
    finalize_invoice,
    get_business_profile,
    get_invoice,
    list_invoice_records,
    preview_finalization,
    remove_line_from_draft,
    save_business_profile,
    stage_approved_sessions_to_monthly_drafts,
    trusted_invoice_document_action,
    update_invoice_draft,
    update_invoice_filing_owner,
    update_invoice_line_item,
    void_invoice,
)
from .invoice_rendering import build_print_preview_html, build_invoice_render_model
from .invoice_pdf import generate_draft_packet_pdf_bytes, generate_draft_pdf_bytes
from .backups import (
    backup_status,
    create_verified_backup,
    maybe_create_daily_launch_backup,
    open_backup_folder,
)
from .financial_summary import get_financial_summary
from .payment_services import (
    apply_available_funds,
    get_payment_detail_view,
    list_all_payments,
    list_invoice_payment_history,
    list_outstanding_invoices,
    list_paid_invoices,
    list_payment_service_period_options,
    record_invoice_payment,
    reverse_allocation,
    void_payment,
)
from .receipt_services import (
    create_payment_receipt,
    preview_payment_receipt,
    trusted_receipt_document_action,
)
from .csv_reports import (
    available_report_types,
    available_years,
    default_report_year,
    generate_report_csv,
    report_filename,
    write_reports,
)
from .importer import calendar_reconciliation_report
from .service_catalog import list_services, set_service_active
from .request_validation import (
    RequestValidationError,
    parse_approve_session_request,
    parse_save_interpretation_request,
    parse_save_person_section_request,
    parse_save_relationship_section_request,
    parse_save_billing_section_request,
    parse_save_session_draft_request,
    parse_mark_candidate_request,
    parse_restore_candidate_request,
    parse_create_person_request,
    parse_update_person_request,
    parse_save_person_alias_request,
    parse_merge_people_request,
    parse_create_account_request,
    parse_create_account_from_client_request,
    parse_update_account_request,
    parse_update_billing_relationship_request,
    parse_remove_account_member_request,
    parse_add_account_member_request,
    parse_setup_billing_relationship_request,
    parse_normalize_payer_request,
    parse_create_billing_party_request,
    parse_update_billing_party_request,
    parse_copy_contact_request,
    parse_create_rate_rule_request,
    parse_preview_rate_request,
    parse_replace_rate_rule_request,
    parse_end_rate_rule_request,
    parse_create_invoice_draft_request,
    parse_correct_invoice_request,
    parse_stage_invoices_request,
    parse_update_invoice_draft_request,
    parse_update_invoice_line_item_request,
    parse_add_sessions_to_draft_request,
    parse_remove_line_from_draft_request,
    parse_preview_finalize_request,
    parse_finalize_invoice_request,
    parse_void_invoice_request,
    parse_update_invoice_filing_owner_request,
    parse_document_action_request,
    parse_print_preview_request,
    parse_record_payment_request,
    parse_reverse_allocation_request,
    parse_apply_funds_request,
    parse_void_payment_request,
    parse_create_payment_receipt_request,
    parse_save_business_profile_request,
    parse_sync_run_request,
    parse_sync_rebuild_request,
)
from .diagnostics import (
    create_issue_report,
    record_event as record_diagnostic_event,
    record_exception as record_diagnostic_exception,
    record_http_event,
)


STATIC_DIR = Path(__file__).parent / "static"
REVIEW_SYNC_TRANSPORT = default_transport

MAX_REQUEST_BODY_BYTES = 1_048_576  # 1 MiB
WRITE_TOKEN_HEADER = "X-Jordana-Write-Token"
_SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
)
_PDF_SAFE_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)
_PREVIEW_TOKEN_TTL_SECONDS = 10 * 60
_preview_tokens: dict[str, tuple[float, str, dict[str, object]]] = {}
_preview_tokens_lock = threading.Lock()


def _store_finalization_preview_payload(invoice_id: str, payload: dict[str, object]) -> str:
    token = secrets.token_urlsafe(24)
    expires_at = time.monotonic() + _PREVIEW_TOKEN_TTL_SECONDS
    with _preview_tokens_lock:
        now = time.monotonic()
        expired = [key for key, (expiry, _, _) in _preview_tokens.items() if expiry < now]
        for key in expired:
            _preview_tokens.pop(key, None)
        _preview_tokens[token] = (expires_at, invoice_id, dict(payload))
    return token


def _load_finalization_preview_payload(invoice_id: str, token: str | None) -> dict[str, object]:
    if not token:
        return {}
    with _preview_tokens_lock:
        entry = _preview_tokens.get(token)
        if not entry:
            raise ValueError("Preview PDF token is invalid or expired.")
        expires_at, stored_invoice_id, payload = entry
        if expires_at < time.monotonic():
            _preview_tokens.pop(token, None)
            raise ValueError("Preview PDF token is invalid or expired.")
        if stored_invoice_id != invoice_id:
            raise ValueError("Preview PDF token is invalid for this invoice.")
        return dict(payload)


class CalendarSyncRuntime:
    def __init__(
        self,
        database_path: str,
        *,
        transport=default_transport,
        interval_minutes: int | None = None,
    ) -> None:
        self.database_path = database_path
        self.transport = transport
        self.interval_minutes = interval_minutes or sync_interval_minutes_from_env()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._running = False
        self._last_error = ""
        self._next_sync_at = next_sync_time_iso(self.interval_minutes)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="jordana-calendar-sync",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _set_running(self, running: bool) -> None:
        with self._status_lock:
            self._running = running

    def _record_error(self, message: str) -> None:
        with self._status_lock:
            self._last_error = sanitize_sync_error(message)

    def status_overlay(self) -> dict[str, str | bool | int]:
        with self._status_lock:
            return {
                "is_syncing": self._running,
                "runtime_error": self._last_error,
                "next_automatic_sync": self._next_sync_at,
                "interval_minutes": self.interval_minutes,
            }

    def _run_once(self, *, startup: bool = False) -> None:
        self._set_running(True)
        try:
            config = review_sync_config(self.database_path)
            if startup:
                sync_calendar_automatically(config, transport=self.transport)
            else:
                sync_with_process_lock(
                    config,
                    full=False,
                    transport=self.transport,
                    skip_if_running=True,
                )
            self._record_error("")
            record_diagnostic_event(
                "calendar_sync",
                "automatic_sync_completed",
                path="/api/sync/run",
                status=200,
            )
        except Exception as error:
            self._record_error(str(error))
            record_diagnostic_event(
                "calendar_sync",
                "automatic_sync_error",
                severity="error",
                path="/api/sync/run",
                status=503,
                message=sanitize_sync_error(str(error)),
            )
        finally:
            self._set_running(False)

    def _run(self) -> None:
        self._run_once(startup=True)
        while not self._stop.wait(self.interval_minutes * 60):
            with self._status_lock:
                self._next_sync_at = next_sync_time_iso(self.interval_minutes)
            self._run_once(startup=False)


def sync_status_payload(conn, runtime: CalendarSyncRuntime | None = None) -> dict:
    payload = public_sync_status(sync_status_for_connection(conn))
    if runtime:
        overlay = runtime.status_overlay()
        payload["next_automatic_sync"] = overlay["next_automatic_sync"]
        if overlay["is_syncing"]:
            payload["current_status"] = "Syncing"
        elif overlay["runtime_error"]:
            payload["current_status"] = "Attention Needed"
            payload["last_error"] = overlay["runtime_error"]
        payload["sync_interval_minutes"] = overlay["interval_minutes"]
    return payload


def review_sync_config(database_path: str):
    return load_sync_config_for_database(database_path)


def is_safe_validation_error(error: Exception) -> bool:
    if isinstance(error, RequestValidationError):
        return True
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
            "A reason is required to return an approved session to Review.",
            "No session found for this candidate.",
            "Only approved sessions can be returned to Review with this action.",
            "Select which participant should receive this future rate.",
            "session_ids must be a list.",
            "Each session_id must be a non-empty string.",
            "Select at least one draft invoice.",
            "Every selected invoice id must be a non-empty string.",
            "Only draft invoices can be included in a draft packet.",
            "Explicit finalization confirmation is required.",
            # People / Aliases / Merge
            "Person not found.",
            "Display name is required.",
            "Cannot merge a person into itself.",
            "Both people must exist before merging.",
            "An active client with this name already exists. Use that client or resolve the duplicate explicitly.",
            "First and last name are required before assigning a person code.",
            # Business Profile
            "Business name is required.",
            # Invoices / Drafts / Finalization
            "Invoice was not found.",
            "Select an active bill-to party.",
            "Bill To cannot change while draft lines are linked to sessions billed to another party.",
            "billing_month must be in YYYY-MM format.",
            "A valid billing period is required.",
            "Invalid delivery method.",
            "Invalid delivery method scope.",
            "Session is already included in this draft.",
            "Source session was not found.",
            "All invoice sessions must use the selected bill-to party.",
            "Session is outside the invoice billing period.",
            "Invoice line was not found.",
            "A void reason is required.",
            "Only a finalized invoice can be voided.",
            "A correction reason is required.",
            "Only a finalized invoice can be corrected.",
            "This invoice cannot be corrected because payment history is attached to it.",
            "The original invoice is no longer available for correction.",
            "The original invoice changed before correction could be completed.",
            "Delete the open correction draft before voiding this invoice.",
            "Only a draft invoice can be changed.",
            "supplement_sequence cannot be negative.",
            "Description must be non-empty.",
            "Amount must be non-negative.",
            "A correction reason is required when the amount changes.",
            "A correction reason is required when the linked session is changed.",
            "Invalid amount scope.",
            "Session-update scope is only available for lines linked to a session.",
            "Line item does not belong to this invoice.",
            "Invoice has changed. Please reload and try again.",
            "Payment date is required.",
            "Payment method is required.",
            "Unsupported payment method.",
            "Payment amount must be greater than zero.",
            "Cannot record a payment for a draft invoice.",
            "Cannot record a payment for a void invoice.",
            "Only a finalized invoice can accept a payment.",
            "Invoice is already fully paid.",
            "Payment amount cannot exceed the current invoice balance.",
            "Invoice line is missing a source session and cannot accept a payment.",
            "Payment Bill To party does not match the invoice Bill To party.",
            # Payment corrections
            "A reversal reason is required.",
            "A void reason is required.",
            "Allocation was not found.",
            "Allocation is already reversed.",
            "Payment was not found.",
            "Payment is already void.",
            "Cannot void a payment with active allocations. Reverse all allocations first.",
            "Payment is not posted.",
            "Amount must be greater than zero.",
            "Amount exceeds available unapplied funds.",
            "Amount exceeds the current invoice balance.",
            "This request has already been processed.",
            "Calendar sync is already running.",
            "Explicit rebuild confirmation is required.",
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
            "Return to Review is blocked: ",
            "This appointment is scheduled for ",
            "Invalid year:",
            "Unsupported date range:",
            "Year must be an integer, got ",
            "Year out of range: ",
            "Invalid report header for ",
        )
        if any(msg.startswith(prefix) for prefix in safe_prefixes):
            return True
    return False


def sanitize_staging_error_message(msg: str) -> str:
    if is_safe_validation_error(ValueError(msg)):
        return msg
    return "An unexpected error occurred during invoice staging."



def make_handler(
    database_path: str,
    write_token: str | None = None,
    sync_runtime: CalendarSyncRuntime | None = None,
):
    launch_write_token = write_token or secrets.token_urlsafe(32)
    shutdown_lock = threading.Lock()
    shutdown_started = False

    def schedule_shutdown(handler: BaseHTTPRequestHandler) -> tuple[bool, bool, str]:
        nonlocal shutdown_started
        server = getattr(handler, "server", None)
        if server is None or not hasattr(server, "shutdown"):
            return False, False, "Application shutdown is not available in this process."
        with shutdown_lock:
            if shutdown_started:
                return True, True, "Shutdown is already in progress."
            shutdown_started = True

        def shutdown_worker() -> None:
            try:
                if sync_runtime is not None:
                    sync_runtime.stop()
                server.shutdown()
            except Exception as error:  # pragma: no cover - surfaced through server state for diagnostics.
                setattr(server, "jordana_shutdown_error", str(error))

        thread = threading.Thread(
            target=shutdown_worker,
            name="jordana-app-shutdown",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            with shutdown_lock:
                shutdown_started = False
            return False, False, "Application shutdown could not be started."
        return True, False, "Jordana Billing is shutting down."

    class ReviewHandler(BaseHTTPRequestHandler):
        write_token = launch_write_token
        write_token_header = WRITE_TOKEN_HEADER
        _security_headers_applied = False

        def log_message(self, format: str, *args: object) -> None:
            return

        def parse_request(self) -> bool:
            self._security_headers_applied = False
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
            elif isinstance(error, SyncError):
                status = 503
                msg = sanitize_sync_error(str(error))
            elif is_safe_validation_error(error):
                status = 400
                msg = str(error)
                if msg == "Account not found.":
                    status = 404
            else:
                status = default_status
                msg = "An unexpected error occurred."
                record_diagnostic_exception(
                    error,
                    method=getattr(self, "command", ""),
                    path=getattr(self, "path", ""),
                )
            self.send_json({"ok": False, "error": msg}, status=status)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path in {"/", "/review", "/invoices", "/reports", "/unpaid", "/payments"} or parsed.path.startswith("/invoices/"):
                    self.send_static("review.html")
                    return
                if parsed.path in {"/clients", "/people"} or parsed.path.startswith("/clients/") or parsed.path.startswith("/people/"):
                    self.send_static("review.html")
                    return
                if parsed.path.startswith("/static/"):
                    self.send_static(parsed.path.removeprefix("/static/"))
                    return
                if parsed.path == "/api/health":
                    self.send_json({"ok": True, "status": "healthy", **current_build_info()})
                    return
                if parsed.path == "/api/build-info":
                    self.send_json({"ok": True, **current_build_info()})
                    return
                if parsed.path == "/api/diagnostics/areas":
                    self.send_json({
                        "areas": [
                            {"value": "review", "label": "Review"},
                            {"value": "billing_relationships", "label": "Billing Relationships"},
                            {"value": "invoices", "label": "Invoices"},
                            {"value": "payments", "label": "Payments"},
                            {"value": "calendar_sync", "label": "Calendar Sync"},
                            {"value": "other", "label": "Other"},
                        ]
                    })
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
                if parsed.path.startswith("/api/people/") and parsed.path.endswith("/account-summary"):
                    person_id = parsed.path.strip("/").split("/")[2]
                    from .payment_services import client_account_summary as _cas
                    self.send_json(_cas(self.conn(), person_id))
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
                if parsed.path == "/api/billing-relationships/duplicate-analysis":
                    self.send_json(analyze_billing_relationship_duplicates(self.conn()))
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
                if parsed.path.startswith("/api/billing-parties/") and parsed.path.endswith("/copy-contact-preview"):
                    parts = parsed.path.strip("/").split("/")
                    target_id = parts[2]
                    source_id = first(parse_qs(parsed.query), "source_billing_party_id") or ""
                    self.send_json(preview_copy_contact_details(self.conn(), target_id, source_id))
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
                            archive_status=first(query, "archive_status") or "active",
                            limit=int(first(query, "limit") or 30),
                            offset=int(first(query, "offset") or 0),
                        )
                    )
                    return
                if parsed.path == "/api/sync/status":
                    self.send_json(sync_status_payload(self.conn(), sync_runtime))
                    return
                if parsed.path == "/api/backups/status":
                    self.send_json(backup_status(database_path))
                    return
                if parsed.path == "/api/service-catalog":
                    self.send_json(list_services(self.conn(), first(parse_qs(parsed.query), "include_inactive") == "1"))
                    return
                if parsed.path == "/api/financial-summary":
                    query = parse_qs(parsed.query)
                    self.send_json(get_financial_summary(self.conn(), first(query, "month") or None))
                    return
                if parsed.path == "/api/invoices/eligible-sessions":
                    query = parse_qs(parsed.query)
                    self.send_json(eligible_sessions(self.conn(), first(query, "bill_to_party_id"), first(query, "period_start"), first(query, "period_end")))
                    return
                if parsed.path == "/api/invoices":
                    query = parse_qs(parsed.query)
                    self.send_json(list_invoice_records(
                        self.conn(),
                        status=first(query, "status") or None,
                        search=first(query, "search") or None,
                        bill_to_party_id=first(query, "bill_to_party_id") or None,
                        participant_person_id=first(query, "participant_person_id") or None,
                        payment_status=first(query, "payment_status") or None,
                        invoice_date_from=first(query, "invoice_date_from") or None,
                        invoice_date_to=first(query, "invoice_date_to") or None,
                        billing_month=first(query, "billing_month") or None,
                        service_period_from=first(query, "service_period_from") or None,
                        service_period_to=first(query, "service_period_to") or None,
                        sort_by=first(query, "sort_by") or "invoice_date",
                        sort_dir=first(query, "sort_dir") or "desc",
                        limit=int(first(query, "limit") or 50),
                        offset=int(first(query, "offset") or 0),
                    ))
                    return
                if parsed.path == "/api/payments/outstanding-invoices":
                    query = parse_qs(parsed.query)
                    self.send_json({
                        "items": list_outstanding_invoices(self.conn(), billing_month=first(query, "billing_month") or None),
                        "service_period_options": list_payment_service_period_options(self.conn()),
                    })
                    return
                if parsed.path == "/api/payments/paid-invoices":
                    query = parse_qs(parsed.query)
                    self.send_json({
                        "items": list_paid_invoices(self.conn(), billing_month=first(query, "billing_month") or None),
                        "service_period_options": list_payment_service_period_options(self.conn()),
                    })
                    return
                if parsed.path == "/api/payments":
                    query = parse_qs(parsed.query)
                    self.send_json({
                        "items": list_all_payments(self.conn(), billing_month=first(query, "billing_month") or None),
                        "service_period_options": list_payment_service_period_options(self.conn()),
                    })
                    return
                if parsed.path.startswith("/api/payments/") and parsed.path.endswith("/receipt-preview"):
                    payment_id = parsed.path.strip("/").split("/")[2]
                    query = parse_qs(parsed.query)
                    self.send_json(preview_payment_receipt(
                        self.conn(),
                        payment_id,
                        filing_owner_person_id=first(query, "filing_owner_person_id") or None,
                    ))
                    return
                if parsed.path.startswith("/api/payments/") and parsed.path.endswith("/receipt-pdf"):
                    payment_id = parsed.path.strip("/").split("/")[2]
                    receipt = self.conn().execute("SELECT * FROM payment_receipts WHERE payment_id = ?", (payment_id,)).fetchone()
                    if not receipt:
                        self.send_json({"ok": False, "error": "Receipt was not found."}, status=404)
                        return
                    pdf_path = Path(receipt["pdf_path"])
                    if not pdf_path.is_file():
                        self.send_json({"ok": False, "error": "The PDF file for this receipt is missing from the expected location."}, status=404)
                        return
                    self.send_pdf(pdf_path.read_bytes(), pdf_path.name or f"Receipt_{receipt['receipt_number']}.pdf")
                    return
                if parsed.path.startswith("/api/payments/") and not parsed.path.endswith("/outstanding-invoices") and not parsed.path.endswith("/paid-invoices"):
                    payment_id = parsed.path.strip("/").split("/")[2]
                    detail = get_payment_detail_view(self.conn(), payment_id)
                    receipt = self.conn().execute("SELECT * FROM payment_receipts WHERE payment_id = ?", (payment_id,)).fetchone()
                    detail["receipt"] = dict(receipt) if receipt else None
                    self.send_json(detail)
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/print-preview"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    data = get_invoice(self.conn(), invoice_id)
                    if data["invoice"]["status"] != "draft":
                        self.send_json({"ok": False, "error": "Print preview is only available for draft invoices."}, status=400)
                        return
                    insurance_payload = None
                    html = build_print_preview_html(
                        data["invoice"], data["lines"],
                        business_profile=data.get("business_profile"),
                        billing_party=data.get("billing_party"),
                        account_summary=(data.get("render_model") or {}).get("account_summary"),
                        insurance_coding_payload=insurance_payload,
                    )
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._apply_security_headers()
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/draft-pdf"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    data = get_invoice(self.conn(), invoice_id)
                    if data["invoice"]["status"] != "draft":
                        self.send_json({"ok": False, "error": "Draft PDF preview is only available for draft invoices."}, status=400)
                        return
                    insurance_payload = None
                    render_model = build_invoice_render_model(
                        data["invoice"], data["lines"],
                        business_profile=data.get("business_profile"),
                        billing_party=data.get("billing_party"),
                        account_summary=(data.get("render_model") or {}).get("account_summary"),
                        insurance_coding_payload=insurance_payload,
                    )
                    body = generate_draft_pdf_bytes(
                        data["invoice"], data["lines"],
                        render_model=render_model,
                    )
                    self.send_pdf(body, f"Invoice_{invoice_id}_draft.pdf")
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/finalization-preview-pdf"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    query = parse_qs(parsed.query)
                    preview_payload = _load_finalization_preview_payload(invoice_id, first(query, "token"))
                    data = get_invoice(self.conn(), invoice_id)
                    if data["invoice"]["status"] != "draft":
                        self.send_json({"ok": False, "error": "Finalization PDF preview is only available for draft invoices."}, status=400)
                        return
                    render_model = build_invoice_render_model(
                        data["invoice"], data["lines"],
                        business_profile=data.get("business_profile"),
                        billing_party=data.get("billing_party"),
                        account_summary=(data.get("render_model") or {}).get("account_summary"),
                        insurance_coding_payload=preview_payload,
                    )
                    body = generate_draft_pdf_bytes(
                        data["invoice"], data["lines"],
                        render_model=render_model,
                    )
                    self.send_pdf(body, f"Invoice_{invoice_id}_finalization_preview.pdf")
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/final-pdf"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    row = self.conn().execute("SELECT status, pdf_path FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
                    if not row:
                        self.send_json({"ok": False, "error": "Invoice was not found."}, status=404)
                        return
                    if row["status"] not in ("finalized", "void"):
                        self.send_json({"ok": False, "error": "Only finalized or void invoices have a PDF."}, status=400)
                        return
                    pdf_path_str = row["pdf_path"]
                    if not pdf_path_str:
                        self.send_json({"ok": False, "error": "No PDF file is stored for this invoice."}, status=404)
                        return
                    pdf_path = Path(pdf_path_str)
                    if not pdf_path.is_file():
                        self.send_json({"ok": False, "error": "The PDF file for this invoice is missing from the expected location."}, status=404)
                        return
                    body = pdf_path.read_bytes()
                    self.send_pdf(body, pdf_path.name or f"Invoice_{invoice_id}.pdf")
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/payments"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    self.send_json(list_invoice_payment_history(self.conn(), invoice_id))
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
                if parsed.path == "/api/app/quit":
                    ok, already_started, message = schedule_shutdown(self)
                    if not ok:
                        self.send_json({"ok": False, "error": message}, status=503)
                        return
                    self.send_json(
                        {
                            "ok": True,
                            "shutting_down": True,
                            "already_started": already_started,
                            "message": message,
                        }
                    )
                    return
                if parsed.path == "/api/diagnostics/report-issue":
                    result = create_issue_report(
                        self.conn(),
                        area=data.get("area") or "other",
                        description=data.get("description") or "",
                        ui_state=data.get("ui_state") if isinstance(data.get("ui_state"), dict) else {},
                        frontend_events=data.get("frontend_events") if isinstance(data.get("frontend_events"), list) else [],
                    )
                    self.send_json(result)
                    return
                if parsed.path == "/api/backups/create":
                    result = create_verified_backup(database_path, reason="manual_backup", protected=True)
                    self.send_json({"ok": True, **backup_status(database_path), "backup_path": str(result.backup_path)})
                    return
                if parsed.path == "/api/backups/open-folder":
                    self.send_json(open_backup_folder())
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/print-preview"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    inv_data = get_invoice(self.conn(), invoice_id)
                    if inv_data["invoice"]["status"] != "draft":
                        self.send_json({"ok": False, "error": "Print preview is only available for draft invoices."}, status=400)
                        return
                    req = parse_print_preview_request(data)
                    finalization_payload = req.to_payload()
                    html = build_print_preview_html(
                        inv_data["invoice"], inv_data["lines"],
                        business_profile=inv_data.get("business_profile"),
                        billing_party=inv_data.get("billing_party"),
                        account_summary=(inv_data.get("render_model") or {}).get("account_summary"),
                        insurance_coding_payload=finalization_payload,
                    )
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._apply_security_headers()
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/draft-pdf"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    inv_data = get_invoice(self.conn(), invoice_id)
                    if inv_data["invoice"]["status"] != "draft":
                        self.send_json({"ok": False, "error": "Draft PDF preview is only available for draft invoices."}, status=400)
                        return
                    req = parse_print_preview_request(data)
                    finalization_payload = req.to_payload()
                    render_model = build_invoice_render_model(
                        inv_data["invoice"], inv_data["lines"],
                        business_profile=inv_data.get("business_profile"),
                        billing_party=inv_data.get("billing_party"),
                        account_summary=(inv_data.get("render_model") or {}).get("account_summary"),
                        insurance_coding_payload=finalization_payload,
                    )
                    body = generate_draft_pdf_bytes(
                        inv_data["invoice"], inv_data["lines"],
                        render_model=render_model,
                    )
                    self.send_pdf(body, f"Invoice_{invoice_id}_draft.pdf")
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/finalization-preview-token"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    inv_data = get_invoice(self.conn(), invoice_id)
                    if inv_data["invoice"]["status"] != "draft":
                        self.send_json({"ok": False, "error": "Finalization PDF preview is only available for draft invoices."}, status=400)
                        return
                    req = parse_print_preview_request(data)
                    payload = req.to_payload()
                    token = _store_finalization_preview_payload(invoice_id, payload)
                    revision = inv_data["invoice"].get("revision") or int(time.time())
                    self.send_json({
                        "ok": True,
                        "preview_pdf_url": (
                            f"/api/invoices/{invoice_id}/finalization-preview-pdf"
                            f"?token={token}&v={revision}"
                        ),
                    })
                    return
                if parsed.path == "/api/people":
                    req = parse_create_person_request(data)
                    self.send_json(create_person(self.conn(), req.to_payload()))
                    return
                if parsed.path == "/api/review/archive-personal-admin":
                    self.send_json(archive_already_classified_personal_admin(self.conn()))
                    return
                if parsed.path == "/api/sessions/archive":
                    candidate_ids = data.get("candidate_ids")
                    if not isinstance(candidate_ids, list) or not all(isinstance(value, str) for value in candidate_ids):
                        raise ValueError("candidate_ids must be a list of session row IDs.")
                    self.send_json(set_sessions_archive_state(self.conn(), candidate_ids, archived=True))
                    return
                if parsed.path == "/api/sessions/restore-archive":
                    candidate_ids = data.get("candidate_ids")
                    if not isinstance(candidate_ids, list) or not all(isinstance(value, str) for value in candidate_ids):
                        raise ValueError("candidate_ids must be a list of session row IDs.")
                    self.send_json(set_sessions_archive_state(self.conn(), candidate_ids, archived=False))
                    return
                if parsed.path == "/api/review/reconcile-calendar":
                    from .importer import suppress_pending_events_missing_from_newest_covering_snapshot

                    conn = self.conn()
                    changed = suppress_pending_events_missing_from_newest_covering_snapshot(conn)
                    conn.commit()
                    self.send_json({"reconciled": changed})
                    return
                if parsed.path.startswith("/api/people/") and parsed.path.endswith("/aliases"):
                    person_id = parsed.path.strip("/").split("/")[2]
                    req = parse_save_person_alias_request(data)
                    self.send_json(
                        save_person_alias(
                            self.conn(),
                            person_id,
                            raw_alias=req.raw_alias,
                            approved_by_user=req.approved_by_user,
                            alias_id=req.to_payload().get("alias_id"),
                        )
                    )
                    return
                if parsed.path.startswith("/api/people/") and parsed.path.endswith("/merge"):
                    survivor_id = parsed.path.strip("/").split("/")[2]
                    req = parse_merge_people_request(data)
                    self.send_json(
                        merge_people(
                            self.conn(),
                            survivor_id,
                            req.duplicate_person_id,
                            req.reason,
                        )
                    )
                    return
                if parsed.path.startswith("/api/people/") and parsed.path.endswith("/archive"):
                    person_id = parsed.path.strip("/").split("/")[2]
                    self.send_json(archive_person(self.conn(), person_id, data.get("reason") or ""))
                    return
                if parsed.path.startswith("/api/people/"):
                    person_id = parsed.path.rsplit("/", 1)[-1]
                    req = parse_update_person_request(data)
                    self.send_json(update_person(self.conn(), person_id, req.to_payload()))
                    return
                if parsed.path == "/api/accounts":
                    req = parse_create_account_request(data)
                    self.send_json(create_account(self.conn(), req.account_name, req.account_type))
                    return
                if parsed.path == "/api/accounts/from-client":
                    req = parse_create_account_from_client_request(data)
                    result = create_account_or_return_existing(
                        self.conn(),
                        req.person_id,
                        req.account_name,
                        req.to_payload().get("account_type", "individual"),
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
                    if action == "delete-or-archive":
                        self.send_json(delete_or_archive_billing_relationship(self.conn(), account_id))
                        return
                    if action == "update-billing-relationship":
                        req = parse_update_billing_relationship_request(data)
                        self.send_json(update_billing_relationship(self.conn(), account_id, req.to_payload()))
                        return
                    if action == "remove-member":
                        req = parse_remove_account_member_request(data)
                        remove_account_member(self.conn(), account_id, req.person_id)
                        self.send_json({"ok": True})
                        return
                    req = parse_update_account_request(data)
                    self.send_json(update_account(self.conn(), account_id, req.to_payload()))
                    return
                if parsed.path == "/api/billing-parties":
                    req = parse_create_billing_party_request(data)
                    self.send_json(create_billing_party(self.conn(), req.to_payload()))
                    return
                if parsed.path.startswith("/api/billing-parties/") and parsed.path.endswith("/copy-contact"):
                    parts = parsed.path.strip("/").split("/")
                    target_id = parts[2]
                    req = parse_copy_contact_request(data)
                    source_id = req.source_billing_party_id
                    confirmed_fields = req.to_payload().get("confirmed_fields")
                    copy_delivery = bool(req.to_payload().get("copy_delivery_method", False))
                    self.send_json(apply_copy_contact_details(
                        self.conn(), target_id, source_id,
                        confirmed_fields=confirmed_fields,
                        copy_delivery_method=copy_delivery,
                    ))
                    return
                if parsed.path.startswith("/api/billing-parties/"):
                    billing_party_id = parsed.path.rsplit("/", 1)[-1]
                    req = parse_update_billing_party_request(data)
                    self.send_json(update_billing_party(self.conn(), billing_party_id, req.to_payload()))
                    return
                if parsed.path == "/api/rate-rules":
                    req = parse_create_rate_rule_request(data)
                    self.send_json(create_rate_rule_from_payload(self.conn(), req.to_payload()))
                    return
                if parsed.path == "/api/rate-rules/preview":
                    req = parse_preview_rate_request(data)
                    self.send_json(preview_rate_suggestion(self.conn(), req.to_payload()))
                    return
                if parsed.path == "/api/reports/generate":
                    paths = write_reports(self.conn(), year=data.get("year"))
                    self.send_json({
                        "ok": True,
                        "files": [str(path) for path in paths],
                    })
                    return
                if parsed.path == "/api/calendar-reconcile/dry-run":
                    self.send_json(calendar_reconciliation_report(
                        self.conn(),
                        month=data.get("month") or None,
                        apply=False,
                    ))
                    return
                if parsed.path == "/api/calendar-reconcile/apply":
                    if data.get("confirm_apply") != "APPLY_CALENDAR_RECONCILE":
                        raise ValueError("Safe recovery requires dry-run confirmation.")
                    self.send_json(calendar_reconciliation_report(
                        self.conn(),
                        month=data.get("month") or None,
                        apply=True,
                    ))
                    return
                if parsed.path.startswith("/api/rate-rules/"):
                    parts = parsed.path.strip("/").split("/")
                    rule_id = parts[2]
                    action = parts[3] if len(parts) > 3 else ""
                    if action == "replace":
                        req = parse_replace_rate_rule_request(data)
                        self.send_json(replace_rate_rule_from_payload(self.conn(), rule_id, req.to_payload()))
                        return
                    if action == "end":
                        req = parse_end_rate_rule_request(data)
                        self.send_json(end_rate_rule(self.conn(), rule_id, req.effective_through))
                        return
                if parsed.path == "/api/business-profile":
                    req = parse_save_business_profile_request(data)
                    self.send_json(save_business_profile(self.conn(), req.to_payload()))
                    return
                if parsed.path == "/api/sync/run":
                    result = sync_calendar_automatically(
                        review_sync_config(database_path),
                        transport=REVIEW_SYNC_TRANSPORT,
                    )
                    self.send_json(
                        {
                            "rows_fetched": result.rows_fetched,
                            "rows_imported": result.rows_imported,
                            "duplicate_snapshots_skipped": result.duplicate_rows_skipped,
                            "review_items_changed": result.review_items_changed,
                            "mode": result.mode,
                            "status": sync_status_payload(self.conn(), sync_runtime),
                        }
                    )
                    return
                if parsed.path == "/api/sync/rebuild":
                    req = parse_sync_rebuild_request(data)
                    if not req.confirmed:
                        raise ValueError("Explicit rebuild confirmation is required.")
                    result, backup_path = rebuild_calendar_data_from_sheet(
                        review_sync_config(database_path),
                        transport=REVIEW_SYNC_TRANSPORT,
                    )
                    self.send_json(
                        {
                            "rows_fetched": result.rows_fetched,
                            "rows_imported": result.rows_imported,
                            "duplicate_snapshots_skipped": result.duplicate_rows_skipped,
                            "review_items_changed": result.review_items_changed,
                            "mode": result.mode,
                            "backup_created": bool(backup_path),
                            "status": sync_status_payload(self.conn(), sync_runtime),
                        }
                    )
                    return
                if parsed.path.startswith("/api/service-catalog/"):
                    parts = parsed.path.strip("/").split("/")
                    self.send_json(set_service_active(self.conn(), parts[2], parts[3] != "deactivate"))
                    return
                if parsed.path == "/api/invoices":
                    req = parse_create_invoice_draft_request(data)
                    self.send_json(create_invoice_draft(self.conn(), req.to_payload()))
                    return
                if parsed.path == "/api/invoices/stage":
                    req = parse_stage_invoices_request(data)
                    session_ids = req.to_payload().get("session_ids")
                    self.send_json(
                        stage_approved_sessions_to_monthly_drafts(self.conn(), session_ids=session_ids)
                    )
                    return
                if parsed.path == "/api/invoices/draft-packet-pdf":
                    invoice_ids = data.get("invoice_ids")
                    if not isinstance(invoice_ids, list) or not invoice_ids:
                        raise ValueError("Select at least one draft invoice.")
                    if any(not isinstance(item, str) or not item.strip() for item in invoice_ids):
                        raise ValueError("Every selected invoice id must be a non-empty string.")
                    documents = []
                    for invoice_id in invoice_ids:
                        inv_data = get_invoice(self.conn(), invoice_id, sync_draft_delivery=False)
                        if inv_data["invoice"]["status"] != "draft":
                            raise ValueError("Only draft invoices can be included in a draft packet.")
                        documents.append((inv_data["invoice"], inv_data["lines"], inv_data.get("render_model")))
                    body = generate_draft_packet_pdf_bytes(documents)
                    self.send_pdf(body, "Jordana_Draft_Invoice_Packet.pdf")
                    return
                if parsed.path.startswith("/api/invoices/") and parsed.path.endswith("/payments"):
                    invoice_id = parsed.path.strip("/").split("/")[2]
                    req = parse_record_payment_request(data)
                    self.send_json(
                        record_invoice_payment(
                            self.conn(),
                            invoice_id=invoice_id,
                            payment_date=req.to_payload().get("payment_date") or "",
                            amount_cents=req.to_payload().get("amount_cents"),
                            payment_method=req.to_payload().get("payment_method") or "",
                            reference_number=req.to_payload().get("reference_number"),
                            received_from_name=req.to_payload().get("received_from_name"),
                            administrative_note=req.to_payload().get("administrative_note"),
                        )
                    )
                    return
                if parsed.path.startswith("/api/payments/allocations/") and parsed.path.endswith("/reverse"):
                    parts = parsed.path.strip("/").split("/")
                    if len(parts) == 5 and parts[4] == "reverse":
                        allocation_id = parts[3]
                        req = parse_reverse_allocation_request(data)
                        self.send_json(
                            reverse_allocation(
                                self.conn(),
                                allocation_id,
                                reason=req.reason,
                                idempotency_key=req.to_payload().get("idempotency_key"),
                            )
                        )
                        return
                if parsed.path.startswith("/api/payments/") and parsed.path.endswith("/apply-funds"):
                    payment_id = parsed.path.strip("/").split("/")[2]
                    req = parse_apply_funds_request(data)
                    self.send_json(
                        apply_available_funds(
                            self.conn(),
                            payment_id,
                            invoice_id=req.invoice_id,
                            amount_cents=req.amount_cents,
                            idempotency_key=req.to_payload().get("idempotency_key"),
                        )
                    )
                    return
                if parsed.path.startswith("/api/payments/") and parsed.path.endswith("/receipt"):
                    payment_id = parsed.path.strip("/").split("/")[2]
                    req = parse_create_payment_receipt_request(data)
                    self.send_json(
                        create_payment_receipt(
                            self.conn(),
                            payment_id,
                            filing_owner_person_id=req.to_payload().get("filing_owner_person_id"),
                        )
                    )
                    return
                if parsed.path.startswith("/api/payments/") and parsed.path.endswith("/receipt-document-action"):
                    payment_id = parsed.path.strip("/").split("/")[2]
                    receipt = self.conn().execute("SELECT receipt_id FROM payment_receipts WHERE payment_id = ?", (payment_id,)).fetchone()
                    if not receipt:
                        self.send_json({"ok": False, "error": "Receipt was not found."}, status=404)
                        return
                    req = parse_document_action_request(data)
                    self.send_json(trusted_receipt_document_action(self.conn(), receipt["receipt_id"], req.action))
                    return
                if parsed.path.startswith("/api/payments/") and parsed.path.endswith("/void"):
                    payment_id = parsed.path.strip("/").split("/")[2]
                    req = parse_void_payment_request(data)
                    self.send_json(
                        void_payment(
                            self.conn(),
                            payment_id,
                            reason=req.reason,
                            idempotency_key=req.to_payload().get("idempotency_key"),
                        )
                    )
                    return
                if parsed.path.startswith("/api/invoices/"):
                    parts = parsed.path.strip("/").split("/")
                    invoice_id = parts[2]
                    action = parts[3] if len(parts) > 3 else "update"
                    if action == "update-line":
                        req = parse_update_invoice_line_item_request(data)
                        self.send_json(
                            update_invoice_line_item(
                                self.conn(),
                                invoice_id,
                                line_id=req.invoice_line_item_id,
                                description=req.description,
                                amount_cents=req.amount_cents,
                                amount_scope=req.amount_scope,
                                reason=req.reason,
                                expected_revision=req.expected_revision,
                            )
                        )
                        return
                    if action == "add-sessions":
                        req = parse_add_sessions_to_draft_request(data)
                        self.send_json(add_sessions_to_draft(self.conn(), invoice_id, req.session_ids))
                        return
                    if action == "remove-line":
                        req = parse_remove_line_from_draft_request(data)
                        self.send_json(remove_line_from_draft(self.conn(), invoice_id, req.invoice_line_item_id))
                        return
                    if action == "delete-draft":
                        self.send_json(delete_invoice_draft(self.conn(), invoice_id))
                        return
                    if action == "preview-finalize":
                        req = parse_preview_finalize_request(data)
                        self.send_json(preview_finalization(self.conn(), invoice_id, data=req.to_payload()))
                        return
                    if action == "finalize":
                        req = parse_finalize_invoice_request(data)
                        if not req.confirmed:
                            raise ValueError("Explicit finalization confirmation is required.")
                        self.send_json(finalize_invoice(
                            self.conn(), invoice_id,
                            expected_revision=req.to_payload().get("expected_revision"),
                            insurance_coding_included=bool(req.to_payload().get("insurance_coding_included")),
                            insurance_diagnosis_code=str(req.to_payload().get("insurance_diagnosis_code") or ""),
                            cancellation_policy_included=bool(req.to_payload().get("cancellation_policy_included")),
                        ))
                        return
                    if action == "filing-owner":
                        req = parse_update_invoice_filing_owner_request(data)
                        payload = req.to_payload()
                        self.send_json(update_invoice_filing_owner(
                            self.conn(),
                            invoice_id,
                            payload.get("person_id"),
                            owner_kind=payload.get("filing_owner_kind"),
                            owner_id=payload.get("filing_owner_record_id"),
                        ))
                        return
                    if action == "document-action":
                        req = parse_document_action_request(data)
                        self.send_json(trusted_invoice_document_action(self.conn(), invoice_id, req.action))
                        return
                    if action == "void":
                        req = parse_void_invoice_request(data)
                        self.send_json(void_invoice(self.conn(), invoice_id, req.reason))
                        return
                    if action == "correct":
                        req = parse_correct_invoice_request(data)
                        self.send_json(start_invoice_correction(self.conn(), invoice_id, req.reason))
                        return
                    req = parse_update_invoice_draft_request(data)
                    self.send_json(update_invoice_draft(self.conn(), invoice_id, req.to_payload()))
                    return
                if parsed.path == "/api/account-members":
                    req = parse_add_account_member_request(data)
                    self.send_json(
                        {
                            "account_member_id": add_account_member(
                                self.conn(),
                                req.account_id,
                                req.person_id,
                                req.to_payload().get("relationship_role", "primary"),
                                bool(req.to_payload().get("is_primary")),
                            )
                        }
                    )
                    return
                if parsed.path.startswith("/api/review/candidates/"):
                    parts = parsed.path.strip("/").split("/")
                    candidate_id = parts[3]
                    action = parts[4] if len(parts) > 4 else "save"
                    if action == "save":
                        req = parse_save_interpretation_request(data)
                        self.send_json(save_interpretation(self.conn(), candidate_id, req.to_payload()))
                        return
                    if action == "save-person":
                        req = parse_save_person_section_request(data)
                        self.send_json(save_person_section(self.conn(), candidate_id, req.to_payload()))
                        return
                    if action == "save-relationship":
                        req = parse_save_relationship_section_request(data)
                        self.send_json(save_relationship_section(self.conn(), candidate_id, req.to_payload()))
                        return
                    if action == "save-billing":
                        req = parse_save_billing_section_request(data)
                        self.send_json(save_billing_section(self.conn(), candidate_id, req.to_payload()))
                        return
                    if action == "save-session":
                        req = parse_save_session_draft_request(data)
                        self.send_json(save_session_draft(self.conn(), candidate_id, req.to_payload()))
                        return
                    if action == "refresh":
                        refresh_candidate_suggestions(self.conn(), candidate_id)
                        self.conn().commit()
                        self.send_json(get_review_candidate(self.conn(), candidate_id))
                        return
                    if action == "approve":
                        req = parse_approve_session_request(data)
                        result = approve_candidate(self.conn(), candidate_id, req.to_payload())
                        approved_session_id = result.get("session", {}).get("id")
                        if approved_session_id:
                            session_row = self.conn().execute("SELECT payment_status FROM sessions WHERE id = ?", (approved_session_id,)).fetchone()
                            if session_row and session_row["payment_status"] == "paid_at_session":
                                result["invoice_staging"] = {
                                    "status": "not_required",
                                    "summary": {
                                        "errors": [],
                                        "message": "Paid-at-session session; invoice staging was not required."
                                    }
                                }
                            else:
                                try:
                                    staging = stage_approved_sessions_to_monthly_drafts(
                                        self.conn(), session_ids=[approved_session_id],
                                    )
                                    if staging.get("errors"):
                                        for err in staging["errors"]:
                                            err["error"] = sanitize_staging_error_message(err.get("error", ""))
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
                        req = parse_mark_candidate_request(data)
                        self.send_json(
                            mark_candidate(
                                self.conn(),
                                candidate_id,
                                classification=req.classification,
                                reason=req.reason,
                            )
                        )
                        return
                    if action == "restore":
                        req = parse_restore_candidate_request(data)
                        self.send_json(
                            restore_candidate(
                                self.conn(),
                                candidate_id,
                                reason=req.reason,
                            )
                        )
                        return
                    if action == "return-to-review":
                        self.send_json(
                            return_approved_session_to_review(
                                self.conn(),
                                candidate_id,
                                reason=data.get("reason", ""),
                                action_source=data.get("action_source", "review_ui"),
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
                    req = parse_setup_billing_relationship_request(data)
                    self.send_json(setup_billing_relationship(self.conn(), req.to_payload()))
                    return
                if parsed.path == "/api/billing-relationships/normalize-payer":
                    req = parse_normalize_payer_request(data)
                    result = normalize_duplicate_payer_billing_parties(
                        self.conn(), req.person_id,
                        canonical_billing_party_id=req.to_payload().get("canonical_billing_party_id"),
                    )
                    self.send_json({"ok": True, **result})
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
                self.send_json(
                    {"ok": False, "error": "Write access expired. Refresh Jordana Billing and try again."},
                    status=403,
                )
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

        def _build_csp(self, nonce: str | None = None) -> str:
            script_src = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
            return (
                f"default-src 'self'; "
                f"script-src {script_src}; "
                f"style-src 'self' 'unsafe-inline'; "
                f"img-src 'self' data:; "
                f"connect-src 'self'; "
                f"frame-src 'self'; "
                f"frame-ancestors 'none'; "
                f"object-src 'none'; "
                f"base-uri 'self'; "
                f"form-action 'self'"
            )

        def _apply_security_headers(self, nonce: str | None = None) -> None:
            if self._security_headers_applied:
                return
            for key, value in _SECURITY_HEADERS:
                self.send_header(key, value)
            self.send_header("Content-Security-Policy", self._build_csp(nonce))
            self._security_headers_applied = True

        def _apply_pdf_safe_headers(self) -> None:
            if self._security_headers_applied:
                return
            for key, value in _PDF_SAFE_HEADERS:
                self.send_header(key, value)
            self._security_headers_applied = True

        def send_pdf(self, body: bytes, filename: str) -> None:
            safe_filename = Path(filename).name.replace('"', "")
            if not safe_filename.lower().endswith(".pdf"):
                safe_filename = f"{safe_filename}.pdf"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'inline; filename="{safe_filename}"')
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(body)))
            self._apply_pdf_safe_headers()
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self) -> None:
            self._apply_security_headers()
            super().end_headers()

        def send_json(self, payload: object, status: int = 200) -> None:
            record_http_event(getattr(self, "command", ""), getattr(self, "path", ""), status, payload)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._apply_security_headers()
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
            nonce = None
            if path.name == "review.html":
                nonce = secrets.token_urlsafe(16)
                body = self.render_review_html(path, nonce).encode("utf-8")
            else:
                body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            if path.suffix in {".html", ".css", ".js"}:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self._apply_security_headers(nonce=nonce)
            self.end_headers()
            self.wfile.write(body)

        def render_review_html(self, path: Path, nonce: str) -> str:
            html = path.read_text(encoding="utf-8")
            css_path = STATIC_DIR / "review.css"
            js_path = STATIC_DIR / "review.js"
            css_version = str(int(css_path.stat().st_mtime)) if css_path.exists() else "1"
            js_version = str(int(js_path.stat().st_mtime)) if js_path.exists() else "1"
            html = html.replace(
                '<link rel="stylesheet" href="/static/review.css" />',
                f'<link rel="stylesheet" href="/static/review.css?v={css_version}" />',
                1,
            )
            bootstrap = json.dumps({"writeToken": launch_write_token}, ensure_ascii=False)
            bootstrap = bootstrap.replace("</", "<\\/")
            bootstrap_script = f'<script nonce="{nonce}">window.__JORDANA_BOOTSTRAP__={bootstrap};</script>'
            marker = '<script src="/static/review.js"></script>'
            if marker in html:
                versioned_script = f'<script src="/static/review.js?v={js_version}"></script>'
                return html.replace(marker, f"{bootstrap_script}\n    {versioned_script}", 1)
            return f"{html}\n{bootstrap_script}\n"

        def send_csv(self, csv_text: str, filename: str) -> None:
            body = csv_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self._apply_security_headers()
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
    try:
        maybe_create_daily_launch_backup(database_path)
    except Exception as error:
        print(f"Automatic launch backup failed: {error}")
    sync_runtime = CalendarSyncRuntime(database_path, transport=REVIEW_SYNC_TRANSPORT)
    server = ThreadingHTTPServer((host, port), make_handler(database_path, sync_runtime=sync_runtime))
    previous_handlers: dict[int, object] = {}

    def request_process_shutdown(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, name="jordana-signal-shutdown", daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, request_process_shutdown)
    print(f"Review UI running at http://{host}:{port}/review")
    sync_runtime.start()
    try:
        server.serve_forever()
    finally:
        if threading.current_thread() is threading.main_thread():
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
        sync_runtime.stop()
        server.server_close()


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
