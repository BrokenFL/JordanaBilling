from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Any

from .csv_reports import write_reports
from .db import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    DatabaseBusyError,
    DatabaseLock,
    LockError,
    connect,
    migrate_database,
)
from .backfill import backfill_phase2
from .importer import import_rows
from .util import now_iso, text


SOURCE_NAME = "google_calendar_snapshots"
DEFAULT_LIMIT = 500
EMPTY_CURSOR = "1970-01-01T00:00:00.000Z"
EMPTY_SNAPSHOT_KEY = ""
SYNC_INTERVAL_ENV = "JORDANA_CALENDAR_SYNC_INTERVAL_MINUTES"
TLS_CERT_ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


class SyncError(RuntimeError):
    pass


class SyncAlreadyRunning(SyncError):
    pass


_SYNC_RUN_LOCK = threading.Lock()


@dataclass
class SyncConfig:
    apps_script_url: str
    ingest_api_key: str
    database_path: str
    reports_dir: str = "Reports"
    timeout_seconds: int = 30


@dataclass
class SyncResult:
    rows_fetched: int
    rows_imported: int
    next_cursor: str | None
    dry_run: bool
    import_run_id: str | None = None
    mode: str = "incremental"
    duplicate_rows_skipped: int = 0
    review_items_changed: int = 0
    skipped: bool = False


@dataclass(frozen=True)
class SyncCursor:
    ingested_at: str
    snapshot_key: str = EMPTY_SNAPSHOT_KEY

    def as_storage_value(self) -> str:
        if not self.snapshot_key:
            return self.ingested_at
        return json.dumps(
            {"ingested_at": self.ingested_at, "snapshot_key": self.snapshot_key},
            sort_keys=True,
            separators=(",", ":"),
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.as_storage_value() == other or (
                not self.snapshot_key and self.ingested_at == other
            )
        if isinstance(other, SyncCursor):
            return (
                self.ingested_at == other.ingested_at
                and self.snapshot_key == other.snapshot_key
            )
        return NotImplemented


Transport = Callable[[str, dict[str, Any], int], dict[str, Any]]


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalize_tls_certificate_environment() -> None:
    """Treat blank certificate env vars as unset while preserving real paths."""
    for key in TLS_CERT_ENV_VARS:
        if key in os.environ and not os.environ[key].strip():
            os.environ.pop(key, None)


def load_config(env_path: str | Path = ".env") -> SyncConfig:
    load_env_file(env_path)
    normalize_tls_certificate_environment()
    missing = [
        key
        for key in (
            "JORDANA_APPS_SCRIPT_URL",
            "JORDANA_INGEST_API_KEY",
            "JORDANA_DATABASE_PATH",
        )
        if not os.environ.get(key)
    ]
    if missing:
        raise SyncError("Missing environment variables: " + ", ".join(missing))
    return SyncConfig(
        apps_script_url=os.environ["JORDANA_APPS_SCRIPT_URL"],
        ingest_api_key=os.environ["JORDANA_INGEST_API_KEY"],
        database_path=os.environ["JORDANA_DATABASE_PATH"],
        reports_dir=os.environ.get("JORDANA_REPORTS_DIR", "Reports"),
        timeout_seconds=int(os.environ.get("JORDANA_SYNC_TIMEOUT_SECONDS", "30")),
    )


def load_sync_config_for_database(
    database_path: str,
    env_path: str | Path = ".env",
) -> SyncConfig:
    load_env_file(env_path)
    normalize_tls_certificate_environment()
    missing = [
        key
        for key in (
            "JORDANA_APPS_SCRIPT_URL",
            "JORDANA_INGEST_API_KEY",
        )
        if not os.environ.get(key)
    ]
    if missing:
        raise SyncError("Missing environment variables: " + ", ".join(missing))
    return SyncConfig(
        apps_script_url=os.environ["JORDANA_APPS_SCRIPT_URL"],
        ingest_api_key=os.environ["JORDANA_INGEST_API_KEY"],
        database_path=database_path,
        reports_dir=os.environ.get("JORDANA_REPORTS_DIR", "Reports"),
        timeout_seconds=int(os.environ.get("JORDANA_SYNC_TIMEOUT_SECONDS", "30")),
    )


def default_transport(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    normalize_tls_certificate_environment()
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.URLError as error:
        raise SyncError(f"Network failure during sync: {error}") from error
    try:
        return json.loads(response_body)
    except json.JSONDecodeError as error:
        raise SyncError("Invalid JSON response from Apps Script.") from error


def sync_now(
    config: SyncConfig | None = None,
    full: bool = False,
    dry_run: bool = False,
    transport: Transport = default_transport,
) -> SyncResult:
    config = config or load_config()
    migrate_database(config.database_path)
    if not dry_run:
        from .backups import create_verified_backup
        from .db import is_operational_db_path

        if is_operational_db_path(config.database_path):
            create_verified_backup(config.database_path, reason="sync_apply")
    conn = connect(config.database_path)
    return sync_with_connection(
        conn,
        config,
        full=full,
        dry_run=dry_run,
        transport=transport,
    )


def sync_calendar_automatically(
    config: SyncConfig | None = None,
    *,
    dry_run: bool = False,
    transport: Transport = default_transport,
    allow_if_running: bool = False,
) -> SyncResult:
    acquired = _SYNC_RUN_LOCK.acquire(blocking=False)
    if not acquired:
        if allow_if_running:
            return SyncResult(
                rows_fetched=0,
                rows_imported=0,
                next_cursor=None,
                dry_run=dry_run,
                mode="skipped",
                skipped=True,
            )
        raise SyncAlreadyRunning("Calendar sync is already running.")
    try:
        config = config or load_config()
        migrate_database(config.database_path)
        if not dry_run:
            from .backups import create_verified_backup
            from .db import is_operational_db_path

            if is_operational_db_path(config.database_path):
                create_verified_backup(config.database_path, reason="sync_apply")
        conn = connect(config.database_path)
        try:
            initial = should_run_initial_full_sync(conn)
            return sync_with_connection(
                conn,
                config,
                full=initial,
                dry_run=dry_run,
                transport=transport,
            )
        finally:
            conn.close()
    finally:
        _SYNC_RUN_LOCK.release()


def rebuild_calendar_data_from_sheet(
    config: SyncConfig,
    *,
    transport: Transport = default_transport,
) -> tuple[SyncResult, str]:
    from .backups import create_verified_backup

    migrate_database(config.database_path)
    backup_path = create_verified_backup(Path(config.database_path), reason="sheet_rebuild").backup_path
    result = sync_with_process_lock(
        config,
        full=True,
        transport=transport,
        backup_before_apply=False,
    )
    return result, str(backup_path)


def sync_with_process_lock(
    config: SyncConfig,
    *,
    full: bool = False,
    dry_run: bool = False,
    transport: Transport = default_transport,
    skip_if_running: bool = False,
    backup_before_apply: bool = True,
) -> SyncResult:
    acquired = _SYNC_RUN_LOCK.acquire(blocking=False)
    if not acquired:
        if skip_if_running:
            return SyncResult(
                rows_fetched=0,
                rows_imported=0,
                next_cursor=None,
                dry_run=dry_run,
                mode="skipped",
                skipped=True,
            )
        raise SyncAlreadyRunning("Calendar sync is already running.")
    try:
        if backup_before_apply and not dry_run:
            from .backups import create_verified_backup
            from .db import is_operational_db_path

            if is_operational_db_path(config.database_path):
                create_verified_backup(config.database_path, reason="sync_apply")
        conn = connect(config.database_path)
        try:
            return sync_with_connection(
                conn,
                config,
                full=full,
                dry_run=dry_run,
                transport=transport,
            )
        finally:
            conn.close()
    finally:
        _SYNC_RUN_LOCK.release()


def sync_with_connection(
    conn: sqlite3.Connection,
    config: SyncConfig,
    full: bool = False,
    dry_run: bool = False,
    transport: Transport = default_transport,
) -> SyncResult:
    attempt_at = now_iso()
    if not dry_run:
        set_sync_attempt(conn, attempt_at)

    mode = "initial_full" if full else "incremental"
    cursor = SyncCursor(EMPTY_CURSOR) if full else get_cursor(conn)
    all_rows: list[dict[str, Any]] = []
    final_cursor = cursor

    try:
        while True:
            response = transport(
                config.apps_script_url,
                {
                    "api_key": config.ingest_api_key,
                    "record_type": "sync_request",
                    "after_ingested_at": cursor.ingested_at,
                    "after_snapshot_key": cursor.snapshot_key,
                    "limit": DEFAULT_LIMIT,
                },
                config.timeout_seconds,
            )
            rows, next_cursor, has_more = validate_sync_response(response)
            all_rows.extend(rows)
            response_cursor = cursor_from_response(next_cursor, rows)
            if is_cursor_after(response_cursor, final_cursor):
                final_cursor = response_cursor
            if not has_more:
                break
            if not is_cursor_after(response_cursor, cursor):
                raise SyncError("Apps Script returned an invalid pagination cursor.")
            cursor = response_cursor

        if dry_run:
            return SyncResult(
                rows_fetched=len(all_rows),
                rows_imported=0,
                next_cursor=final_cursor.as_storage_value(),
                dry_run=True,
                mode=mode,
            )

        lock = DatabaseLock(config.database_path, timeout_seconds=DEFAULT_LOCK_TIMEOUT_SECONDS)
        try:
            lock.acquire()
        except LockError as error:
            raise SyncError(str(error)) from error

        try:
            with conn:
                before = count_raw_rows(conn)
                review_before = count_review_rows(conn)
                import_run_id = import_rows(
                    conn,
                    all_rows,
                    SOURCE_NAME,
                    source_path=config.apps_script_url,
                    commit=False,
                )
                rows_imported = count_raw_rows(conn) - before
                review_items_changed = abs(count_review_rows(conn) - review_before)
                if final_cursor.ingested_at:
                    set_sync_success(
                        conn,
                        final_cursor.as_storage_value(),
                        rows_imported,
                        rows_fetched=len(all_rows),
                        duplicate_rows=max(len(all_rows) - rows_imported, 0),
                        review_items_changed=review_items_changed,
                        mode=mode,
                    )
                backfill_phase2(conn)
                write_reports(conn, config.reports_dir)
            from .invoice_services import stage_approved_sessions_to_monthly_drafts

            stage_approved_sessions_to_monthly_drafts(conn)
        except sqlite3.OperationalError as error:
            if "database is locked" in str(error).lower() or "locked" in str(error).lower():
                raise DatabaseBusyError(
                    "Database is locked by another operation. "
                    "Please retry in a moment."
                ) from error
            raise
        finally:
            lock.release()

        return SyncResult(
            rows_fetched=len(all_rows),
            rows_imported=rows_imported,
            next_cursor=final_cursor.as_storage_value(),
            dry_run=False,
            import_run_id=import_run_id,
            mode=mode,
            duplicate_rows_skipped=max(len(all_rows) - rows_imported, 0),
            review_items_changed=review_items_changed,
        )
    except DatabaseBusyError as error:
        if not dry_run:
            record_sync_error(conn, attempt_at, error)
        raise SyncError(str(error)) from error
    except Exception as error:
        if not dry_run:
            record_sync_error(conn, attempt_at, error)
        if isinstance(error, SyncError):
            raise
        raise SyncError(str(error)) from error


def validate_sync_response(
    response: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | dict[str, Any] | None, bool]:
    if not isinstance(response, dict):
        raise SyncError("Apps Script response must be an object.")
    if response.get("ok") is not True:
        raise SyncError(text(response.get("error")) or "Apps Script sync failed.")
    if response.get("record_type") != "sync_response":
        raise SyncError("Apps Script returned the wrong record_type.")
    rows = response.get("rows")
    if not isinstance(rows, list):
        raise SyncError("Apps Script response rows must be a list.")
    for row in rows:
        if not isinstance(row, dict):
            raise SyncError("Every synced row must be an object.")
        if not text(row.get("snapshot_key")):
            raise SyncError("Every synced row must include snapshot_key.")
        if not text(row.get("ingested_at")):
            raise SyncError("Every synced row must include ingested_at.")
    next_cursor = response.get("next_cursor")
    if next_cursor is not None and not isinstance(next_cursor, (str, dict)):
        raise SyncError("next_cursor must be a string, object, or null.")
    has_more = response.get("has_more")
    if not isinstance(has_more, bool):
        raise SyncError("has_more must be boolean.")
    return rows, next_cursor, has_more


def parse_cursor(value: Any) -> SyncCursor:
    cursor_value = text(value)
    if not cursor_value:
        return SyncCursor(EMPTY_CURSOR)
    if cursor_value.startswith("{"):
        try:
            parsed = json.loads(cursor_value)
        except json.JSONDecodeError:
            return SyncCursor(cursor_value)
        if isinstance(parsed, dict):
            return SyncCursor(
                text(parsed.get("ingested_at")) or EMPTY_CURSOR,
                text(parsed.get("snapshot_key")),
            )
    return SyncCursor(cursor_value)


def get_cursor(conn: sqlite3.Connection) -> SyncCursor:
    row = conn.execute(
        "SELECT cursor_value FROM sync_state WHERE source_name = ?",
        (SOURCE_NAME,),
    ).fetchone()
    return parse_cursor(row["cursor_value"] if row else None)


def should_run_initial_full_sync(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT cursor_value, last_success_at
        FROM sync_state
        WHERE source_name = ?
        """,
        (SOURCE_NAME,),
    ).fetchone()
    if not row or not text(row["last_success_at"]):
        return True
    cursor = parse_cursor(row["cursor_value"])
    return cursor.ingested_at == EMPTY_CURSOR and not cursor.snapshot_key


def cursor_from_response(
    next_cursor: str | dict[str, Any] | None,
    rows: list[dict[str, Any]],
) -> SyncCursor:
    if isinstance(next_cursor, dict):
        return SyncCursor(
            text(next_cursor.get("ingested_at")) or EMPTY_CURSOR,
            text(next_cursor.get("snapshot_key")),
        )
    if isinstance(next_cursor, str) and next_cursor.startswith("{"):
        return parse_cursor(next_cursor)
    if rows:
        return max(
            (SyncCursor(text(row.get("ingested_at")), text(row.get("snapshot_key"))) for row in rows),
            key=lambda item: (item.ingested_at, item.snapshot_key),
        )
    if isinstance(next_cursor, str) and next_cursor:
        return SyncCursor(next_cursor)
    return SyncCursor(EMPTY_CURSOR)


def is_cursor_after(candidate: SyncCursor, current: SyncCursor) -> bool:
    return (candidate.ingested_at, candidate.snapshot_key) > (
        current.ingested_at,
        current.snapshot_key,
    )


def set_sync_attempt(conn: sqlite3.Connection, attempt_at: str) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (
          source_name, cursor_value, last_attempt_at, rows_imported, updated_at
        ) VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(source_name) DO UPDATE SET
          last_attempt_at = excluded.last_attempt_at,
          updated_at = excluded.updated_at
        """,
        (SOURCE_NAME, EMPTY_CURSOR, attempt_at, attempt_at),
    )
    conn.commit()


def set_sync_success(
    conn: sqlite3.Connection,
    cursor: str,
    rows_imported: int,
    *,
    rows_fetched: int = 0,
    duplicate_rows: int = 0,
    review_items_changed: int = 0,
    mode: str = "incremental",
) -> None:
    now = now_iso()
    conn.execute(
        """
        UPDATE sync_state
        SET cursor_value = ?,
            last_success_at = ?,
            last_error = '',
            rows_imported = rows_imported + ?,
            last_mode = ?,
            last_rows_fetched = ?,
            last_rows_imported = ?,
            last_duplicate_rows = ?,
            last_review_items_changed = ?,
            updated_at = ?
        WHERE source_name = ?
        """,
        (
            cursor,
            now,
            rows_imported,
            mode,
            rows_fetched,
            rows_imported,
            duplicate_rows,
            review_items_changed,
            now,
            SOURCE_NAME,
        ),
    )


def record_sync_error(
    conn: sqlite3.Connection,
    attempt_at: str,
    error: Exception,
) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (
          source_name, cursor_value, last_attempt_at, last_error, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_name) DO UPDATE SET
          last_attempt_at = excluded.last_attempt_at,
          last_error = excluded.last_error,
          updated_at = excluded.updated_at
        """,
        (SOURCE_NAME, EMPTY_CURSOR, attempt_at, str(error), now_iso()),
    )
    conn.commit()


def count_raw_rows(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS count FROM raw_calendar_snapshots").fetchone()["count"])


def count_review_rows(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS count FROM review_queue").fetchone()["count"])


def sync_status_for_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM sync_state WHERE source_name = ?",
        (SOURCE_NAME,),
    ).fetchone()
    status = dict(row) if row else {"source_name": SOURCE_NAME}
    status["raw_snapshot_count"] = count_raw_rows(conn)
    open_review = conn.execute(
        "SELECT COUNT(*) AS count FROM review_queue WHERE status = 'open'"
    ).fetchone()
    status["unresolved_count"] = int(open_review["count"])
    return status


def get_sync_status(config: SyncConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    conn = connect(config.database_path)
    return sync_status_for_connection(conn)


def public_sync_status(status: dict[str, Any]) -> dict[str, Any]:
    last_success = text(status.get("last_success_at"))
    return {
        "current_status": "Attention Needed" if text(status.get("last_error")) else "Idle",
        "last_attempt": text(status.get("last_attempt_at")),
        "last_success": last_success,
        "last_mode": public_mode_label(text(status.get("last_mode"))),
        "rows_fetched": int(status.get("last_rows_fetched") or 0),
        "new_raw_snapshots_imported": int(status.get("last_rows_imported") or 0),
        "duplicate_snapshots_skipped": int(status.get("last_duplicate_rows") or 0),
        "review_items_changed": int(status.get("last_review_items_changed") or 0),
        "total_rows_imported": int(status.get("rows_imported") or 0),
        "raw_snapshot_count": int(status.get("raw_snapshot_count") or 0),
        "open_review_count": int(status.get("unresolved_count") or 0),
        "last_error": sanitize_sync_error(text(status.get("last_error"))),
    }


def public_mode_label(mode: str) -> str:
    if mode == "initial_full":
        return "Initial full sync"
    if mode == "incremental":
        return "Incremental sync"
    return ""


def sanitize_sync_error(message: str) -> str:
    if not message:
        return ""
    safe_fragments = (
        "Calendar sync is already running",
        "Database is locked",
        "Database is busy",
        "Missing environment variables",
        "Apps Script sync failed",
        "Apps Script returned",
        "Invalid JSON response",
        "Network failure during sync",
    )
    if any(fragment in message for fragment in safe_fragments):
        return message
    return "Calendar sync needs attention. Please retry from Calendar Import."


def sync_interval_minutes_from_env(default: int = 15) -> int:
    raw = os.environ.get(SYNC_INTERVAL_ENV, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(value, 1)


def next_sync_time_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def get_last_success_time(config: SyncConfig | None = None) -> str:
    return text(get_sync_status(config).get("last_success_at"))


def get_unresolved_count(config: SyncConfig | None = None) -> int:
    config = config or load_config()
    migrate_database(config.database_path)
    conn = connect(config.database_path)
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM review_queue WHERE status = 'open'"
    ).fetchone()
    return int(row["count"])


def cli_sync_status(config: SyncConfig | None = None) -> str:
    status = get_sync_status(config)
    keys = [
        "source_name",
        "cursor_value",
        "last_attempt_at",
        "last_success_at",
        "rows_imported",
        "raw_snapshot_count",
        "unresolved_count",
        "last_error",
    ]
    return "\n".join(f"{key}: {status.get(key, '')}" for key in keys)


def main(argv: list[str] | None = None) -> int:
    try:
        result = sync_now()
    except SyncError as error:
        print(f"sync failed: {error}", file=sys.stderr)
        return 1
    print(
        f"synced rows_fetched={result.rows_fetched} "
        f"rows_imported={result.rows_imported} dry_run={result.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
