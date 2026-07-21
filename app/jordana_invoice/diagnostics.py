from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import threading
import traceback
from collections import deque
from pathlib import Path
from typing import Any

from .build_info import current_build_info
from .db import MIGRATIONS
from .google_sync import sanitize_sync_error
from .util import now_iso


DIAGNOSTIC_AREAS = {
    "review": "Review",
    "billing_relationships": "Billing Relationships",
    "invoices": "Invoices",
    "payments": "Payments",
    "calendar_sync": "Calendar Sync",
    "other": "Other",
}

_MAX_EVENTS = 160
_MAX_PERSISTED_ERRORS = 200
_RECENT_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_EVENT_LOCK = threading.Lock()
_ID_PATTERN = re.compile(
    r"/([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}|[0-9a-fA-F]{32,}|[A-Za-z0-9_-]{20,})(?=/|$)"
)
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")
_PATH_PATTERN = re.compile(r"(?:(?:/Users|/Volumes|/private|/var|/tmp)/[^\s,;]+)")
_DIAGNOSIS_PATTERN = re.compile(r"\b[A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?\b")


def normalize_area(area: Any) -> str:
    value = str(area or "").strip().lower().replace("-", "_").replace(" ", "_")
    return value if value in DIAGNOSTIC_AREAS else "other"


def area_for_path(path: str) -> str:
    if path.startswith("/api/review") or path == "/review":
        return "review"
    if path.startswith("/api/billing-relationships") or path.startswith("/api/accounts") or path.startswith("/api/billing-parties"):
        return "billing_relationships"
    if path.startswith("/api/invoices") or path == "/invoices":
        return "invoices"
    if path.startswith("/api/payments") or "/payments" in path or path == "/payments":
        return "payments"
    if path.startswith("/api/sync") or path.startswith("/api/calendar-reconcile") or path in {"/api/status"}:
        return "calendar_sync"
    return "other"


def route_template(path: str) -> str:
    parsed = path.split("?", 1)[0]
    return _ID_PATTERN.sub("/{id}", parsed)


def sanitize_text(value: Any, private_terms: list[str] | None = None) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = _EMAIL_PATTERN.sub("[redacted-email]", text)
    text = _PHONE_PATTERN.sub("[redacted-phone]", text)
    text = _PATH_PATTERN.sub("[redacted-path]", text)
    text = _DIAGNOSIS_PATTERN.sub("[redacted-code]", text)
    for term in sorted(set(private_terms or []), key=len, reverse=True):
        if len(term) < 2:
            continue
        text = re.sub(re.escape(term), "[redacted-name]", text, flags=re.IGNORECASE)
    return text[:500]


def _private_terms(conn: sqlite3.Connection) -> list[str]:
    terms: set[str] = set()
    for table, columns in (
        ("people", ("display_name", "first_name", "last_name", "preferred_name")),
        ("billing_parties", ("billing_name", "organization_name")),
    ):
        try:
            available = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            selected = [col for col in columns if col in available]
            if not selected:
                continue
            rows = conn.execute(
                f"SELECT {', '.join(selected)} FROM {table} LIMIT 2000"
            ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            for col in selected:
                value = str(row[col] or "").strip()
                if not value:
                    continue
                terms.add(value)
                for piece in value.split():
                    if len(piece) >= 3:
                        terms.add(piece)
    return sorted(terms)


def record_event(
    area: str,
    event: str,
    *,
    severity: str = "info",
    method: str | None = None,
    path: str | None = None,
    status: int | None = None,
    message: str | None = None,
) -> None:
    entry = {
        "timestamp": now_iso(),
        "area": normalize_area(area),
        "event": str(event or "event")[:80],
        "severity": severity if severity in {"info", "warning", "error"} else "info",
    }
    if method:
        entry["method"] = method
    if path:
        entry["route"] = route_template(path)
    if status is not None:
        entry["status"] = int(status)
    if message:
        entry["message"] = sanitize_text(message)
    with _EVENT_LOCK:
        _RECENT_EVENTS.append(entry)
        _persist_safe_error_event(entry)


def record_http_event(method: str, path: str, status: int, payload: object | None = None) -> None:
    if not path.startswith("/api/"):
        return
    severity = "error" if status >= 400 else "info"
    message = ""
    if status >= 400 and isinstance(payload, dict):
        message = str(payload.get("error") or "")
    record_event(
        area_for_path(path),
        "http_response",
        severity=severity,
        method=method,
        path=path,
        status=status,
        message=message,
    )


def record_exception(error: Exception, *, method: str, path: str) -> None:
    """Record a privacy-safe failure signature without values, paths, or traceback text."""
    frames = traceback.extract_tb(error.__traceback__)[-4:]
    signature = " > ".join(
        f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
        for frame in frames
    )
    entry = {
        "timestamp": now_iso(),
        "area": area_for_path(path),
        "event": "unhandled_exception",
        "severity": "error",
        "method": method,
        "route": route_template(path),
        "exception_type": type(error).__name__[:100],
        "failure_signature": signature[:500],
    }
    with _EVENT_LOCK:
        _RECENT_EVENTS.append(entry)
        _persist_safe_error_event(entry)


def recent_events(area: str) -> dict[str, list[dict[str, Any]]]:
    normalized = normalize_area(area)
    with _EVENT_LOCK:
        events = _deduplicate_events(_read_persisted_error_events() + list(_RECENT_EVENTS))
    scoped = [entry for entry in events if entry.get("area") in {normalized, "other"}]
    warnings = [entry for entry in scoped if entry.get("severity") in {"warning", "error"}]
    errors = [entry for entry in scoped if entry.get("severity") == "error"]
    return {
        "application_events": scoped[-60:],
        "warnings": warnings[-30:],
        "errors": errors[-30:],
    }


def _persistent_error_path() -> Path:
    return diagnostics_dir() / "sanitized-runtime-errors.jsonl"


def _safe_persistent_event(entry: dict[str, Any]) -> dict[str, Any] | None:
    if entry.get("severity") not in {"warning", "error"}:
        return None
    safe: dict[str, Any] = {}
    for key in (
        "timestamp", "area", "event", "severity", "method", "route",
        "status", "exception_type", "failure_signature",
    ):
        value = entry.get(key)
        if value is None or value == "":
            continue
        safe[key] = value if isinstance(value, int) else sanitize_text(value)
    return safe


def _persist_safe_error_event(entry: dict[str, Any]) -> None:
    safe = _safe_persistent_event(entry)
    if not safe:
        return
    try:
        path = _persistent_error_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        existing = path.read_text(encoding="utf-8").splitlines()[-(_MAX_PERSISTED_ERRORS - 1):] if path.exists() else []
        lines = existing + [json.dumps(safe, ensure_ascii=False, sort_keys=True)]
        temporary = path.with_suffix(".tmp")
        temporary.write_text("\n".join(lines[-_MAX_PERSISTED_ERRORS:]) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
    except OSError:
        return


def _read_persisted_error_events() -> list[dict[str, Any]]:
    try:
        lines = _persistent_error_path().read_text(encoding="utf-8").splitlines()[-_MAX_PERSISTED_ERRORS:]
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            safe = _safe_persistent_event(value)
            if safe:
                events.append(safe)
    return events


def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for event in events:
        key = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def _count_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] if row else 0)
    except sqlite3.Error:
        return 0


def _group_counts(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    try:
        rows = conn.execute(
            f"SELECT COALESCE({column}, 'unset') AS key, COUNT(*) AS count FROM {table} GROUP BY COALESCE({column}, 'unset')"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["key"]): int(row["count"]) for row in rows}


def database_activity_summary(conn: sqlite3.Connection, area: str) -> dict[str, Any]:
    normalized = normalize_area(area)
    if normalized == "review":
        return {
            "candidate_review_status_counts": _group_counts(conn, "calendar_event_candidates", "review_status"),
            "session_review_status_counts": _group_counts(conn, "sessions", "review_status"),
            "pending_review_items": _count_rows(conn, "SELECT COUNT(*) FROM review_items WHERE review_status != 'resolved'"),
        }
    if normalized == "billing_relationships":
        return {
            "active_billing_parties": _count_rows(conn, "SELECT COUNT(*) FROM billing_parties WHERE active = 1"),
            "inactive_billing_parties": _count_rows(conn, "SELECT COUNT(*) FROM billing_parties WHERE active = 0"),
            "active_accounts": _count_rows(conn, "SELECT COUNT(*) FROM client_accounts WHERE active = 1"),
            "inactive_accounts": _count_rows(conn, "SELECT COUNT(*) FROM client_accounts WHERE active = 0"),
        }
    if normalized == "invoices":
        return {
            "invoice_status_counts": _group_counts(conn, "invoices", "status"),
            "draft_lines": _count_rows(conn, "SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id IN (SELECT invoice_id FROM invoices WHERE status = 'draft')"),
            "finalized_total_cents": _count_rows(conn, "SELECT COALESCE(SUM(total_cents), 0) FROM invoices WHERE status = 'finalized'"),
        }
    if normalized == "payments":
        return {
            "payment_status_counts": _group_counts(conn, "payments", "status"),
            "allocation_status_counts": _group_counts(conn, "payment_allocations", "status"),
            "posted_total_cents": _count_rows(conn, "SELECT COALESCE(SUM(amount_cents), 0) FROM payments WHERE status = 'posted'"),
        }
    if normalized == "calendar_sync":
        try:
            rows = conn.execute(
                """
                SELECT source_name, last_attempt_at, last_success_at, last_error,
                       rows_imported, last_mode, last_rows_fetched,
                       last_rows_imported, last_duplicate_rows,
                       last_review_items_changed, updated_at
                FROM sync_state
                ORDER BY source_name
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []
        return {
            "sync_sources": [
                {
                    "source_name": row["source_name"],
                    "last_attempt_at": row["last_attempt_at"],
                    "last_success_at": row["last_success_at"],
                    "last_error": sanitize_sync_error(row["last_error"] or "") if row["last_error"] else "",
                    "rows_imported": row["rows_imported"],
                    "last_mode": row["last_mode"],
                    "last_rows_fetched": row["last_rows_fetched"],
                    "last_rows_imported": row["last_rows_imported"],
                    "last_duplicate_rows": row["last_duplicate_rows"],
                    "last_review_items_changed": row["last_review_items_changed"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ],
            "raw_snapshots": _count_rows(conn, "SELECT COUNT(*) FROM raw_calendar_snapshots"),
        }
    return {
        "people_count": _count_rows(conn, "SELECT COUNT(*) FROM people"),
        "sessions_count": _count_rows(conn, "SELECT COUNT(*) FROM sessions"),
        "invoice_status_counts": _group_counts(conn, "invoices", "status"),
    }


def schema_version_info(conn: sqlite3.Connection) -> dict[str, Any]:
    migration_head = MIGRATIONS[-1][0] if MIGRATIONS else ""
    try:
        rows = conn.execute(
            "SELECT migration_id, applied_at FROM schema_migrations ORDER BY applied_at, migration_id"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    return {
        "migration_head": migration_head,
        "applied_migration_count": len(rows),
        "latest_applied_migration": rows[-1]["migration_id"] if rows else "",
    }


def system_health_summary(conn: sqlite3.Connection, private_terms: list[str]) -> dict[str, Any]:
    try:
        quick_check_row = conn.execute("PRAGMA quick_check(1)").fetchone()
        quick_check = str(quick_check_row[0] if quick_check_row else "unavailable")
    except sqlite3.Error:
        quick_check = "unavailable"
    try:
        foreign_key_violations = len(conn.execute("PRAGMA foreign_key_check").fetchmany(101))
        if foreign_key_violations > 100:
            foreign_key_violations = "100+"
    except sqlite3.Error:
        foreign_key_violations = "unavailable"
    sync = database_activity_summary(conn, "calendar_sync")
    for source in sync.get("sync_sources", []):
        source["last_error"] = sanitize_text(source.get("last_error"), private_terms)
    return {
        "runtime": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "operating_system": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "sqlite": sqlite3.sqlite_version,
        },
        "database": {
            "quick_check": sanitize_text(quick_check),
            "foreign_key_violation_count": foreign_key_violations,
        },
        "calendar_sync": sync,
    }


def build_info_for_report() -> dict[str, str]:
    info = current_build_info()
    commit = info.get("git_commit") or ""
    if commit and commit != "source-checkout":
        info["commit_hash"] = commit
        return info
    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result and result.returncode == 0:
        info["commit_hash"] = result.stdout.strip()
    else:
        info["commit_hash"] = commit or "unavailable"
    return info


def diagnostics_dir() -> Path:
    configured = os.environ.get("JORDANA_DIAGNOSTICS_DIR")
    if configured:
        return Path(os.path.expanduser(configured))
    return Path("Reports") / "Diagnostics"


def create_issue_report(
    conn: sqlite3.Connection,
    *,
    area: str,
    description: str = "",
    ui_state: dict[str, Any] | None = None,
    frontend_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = normalize_area(area)
    terms = _private_terms(conn)
    captured = recent_events(normalized)
    backend_events = _sanitize_event_list(captured["application_events"], terms)
    backend_warnings = _sanitize_event_list(captured["warnings"], terms)
    backend_errors = _sanitize_event_list(captured["errors"], terms)
    sanitized_frontend = []
    for event in (frontend_events or [])[-80:]:
        if not isinstance(event, dict):
            continue
        sanitized_frontend.append({
            "timestamp": sanitize_text(event.get("timestamp")),
            "area": normalize_area(event.get("area")),
            "event": sanitize_text(event.get("event")),
            "severity": sanitize_text(event.get("severity")),
            "route": route_template(str(event.get("route") or "")) if event.get("route") else "",
            "status": event.get("status") if isinstance(event.get("status"), int) else None,
            "message": sanitize_text(event.get("message"), terms),
        })

    report = {
        "report_type": "jordana_billing_issue_report",
        "created_at": now_iso(),
        "selected_area": normalized,
        "selected_area_label": DIAGNOSTIC_AREAS[normalized],
        "user_description": sanitize_text(description, terms),
        "build": build_info_for_report(),
        "schema": schema_version_info(conn),
        "current_screen": sanitize_text((ui_state or {}).get("current_screen")),
        "ui_state": _sanitize_ui_state(ui_state or {}),
        "backend_events": backend_events,
        "frontend_events": sanitized_frontend,
        "recent_warnings": backend_warnings + [
            event for event in sanitized_frontend if event.get("severity") in {"warning", "error"}
        ][-20:],
        "recent_errors": backend_errors + [
            event for event in sanitized_frontend if event.get("severity") == "error"
        ][-20:],
        "database_activity": database_activity_summary(conn, normalized),
        "system_health": system_health_summary(conn, terms),
        "privacy_exclusions": [
            "client names",
            "clinical information",
            "invoice PDFs",
            "live SQLite database",
            "raw calendar titles",
            "filesystem paths",
            "credentials",
        ],
    }
    out_dir = diagnostics_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"issue-report-{now_iso().replace(':', '').replace('.', '-')}.json"
    path = out_dir / filename
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    record_event(normalized, "issue_report_created", path="/api/diagnostics/report-issue", status=200)
    return {
        "ok": True,
        "filename": filename,
        "saved_to": str(out_dir),
        "report": report,
        "report_text": json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
    }


def _sanitize_event_list(events: list[dict[str, Any]], private_terms: list[str]) -> list[dict[str, Any]]:
    sanitized = []
    for event in events:
        clean: dict[str, Any] = {}
        for key in ("timestamp", "area", "event", "severity", "method", "route", "message", "exception_type", "failure_signature"):
            if key in event:
                clean[key] = sanitize_text(event.get(key), private_terms)
        if isinstance(event.get("status"), int):
            clean["status"] = event["status"]
        sanitized.append(clean)
    return sanitized


def _sanitize_ui_state(ui_state: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "current_screen",
        "path",
        "hash",
        "review_filters",
        "invoice_filters",
        "payment_filters",
        "session_filters",
        "selected_candidate_present",
        "selected_invoice_present",
        "selected_payment_present",
        "selected_person_present",
        "selected_account_present",
        "overlay_open",
        "dirty_fields_count",
    }
    clean: dict[str, Any] = {}
    for key, value in ui_state.items():
        if key not in allowed:
            continue
        if isinstance(value, dict):
            clean[key] = {
                sanitize_text(k): sanitize_text(v)
                for k, v in value.items()
                if isinstance(k, str)
            }
        elif isinstance(value, bool) or isinstance(value, int):
            clean[key] = value
        else:
            clean[key] = sanitize_text(value)
    return clean
