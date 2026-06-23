from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from .csv_reports import write_reports
from .db import connect, init_db
from .backfill import backfill_phase2
from .importer import import_rows
from .util import now_iso, text


SOURCE_NAME = "google_calendar_snapshots"
DEFAULT_LIMIT = 500
EMPTY_CURSOR = "1970-01-01T00:00:00.000Z"


class SyncError(RuntimeError):
    pass


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


def load_config(env_path: str | Path = ".env") -> SyncConfig:
    load_env_file(env_path)
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
    conn = connect(config.database_path)
    init_db(conn)
    return sync_with_connection(
        conn,
        config,
        full=full,
        dry_run=dry_run,
        transport=transport,
    )


def sync_with_connection(
    conn: sqlite3.Connection,
    config: SyncConfig,
    full: bool = False,
    dry_run: bool = False,
    transport: Transport = default_transport,
) -> SyncResult:
    init_db(conn)
    attempt_at = now_iso()
    set_sync_attempt(conn, attempt_at)

    cursor = EMPTY_CURSOR if full else get_cursor(conn)
    all_rows: list[dict[str, Any]] = []
    next_cursor: str | None = cursor

    try:
        while True:
            response = transport(
                config.apps_script_url,
                {
                    "api_key": config.ingest_api_key,
                    "record_type": "sync_request",
                    "after_ingested_at": cursor,
                    "limit": DEFAULT_LIMIT,
                },
                config.timeout_seconds,
            )
            rows, next_cursor, has_more = validate_sync_response(response)
            all_rows.extend(rows)
            if not has_more:
                break
            if not next_cursor or next_cursor == cursor:
                raise SyncError("Apps Script returned an invalid pagination cursor.")
            cursor = next_cursor

        if dry_run:
            return SyncResult(
                rows_fetched=len(all_rows),
                rows_imported=0,
                next_cursor=next_cursor,
                dry_run=True,
            )

        with conn:
            before = count_raw_rows(conn)
            import_run_id = import_rows(
                conn,
                all_rows,
                SOURCE_NAME,
                source_path=config.apps_script_url,
                commit=False,
            )
            rows_imported = count_raw_rows(conn) - before
            if next_cursor:
                set_sync_success(conn, next_cursor, rows_imported)
            backfill_phase2(conn)
            write_reports(conn, config.reports_dir)

        return SyncResult(
            rows_fetched=len(all_rows),
            rows_imported=rows_imported,
            next_cursor=next_cursor,
            dry_run=False,
            import_run_id=import_run_id,
        )
    except Exception as error:
        record_sync_error(conn, attempt_at, error)
        if isinstance(error, SyncError):
            raise
        raise SyncError(str(error)) from error


def validate_sync_response(
    response: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, bool]:
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
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise SyncError("next_cursor must be a string or null.")
    has_more = response.get("has_more")
    if not isinstance(has_more, bool):
        raise SyncError("has_more must be boolean.")
    return rows, next_cursor, has_more


def get_cursor(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT cursor_value FROM sync_state WHERE source_name = ?",
        (SOURCE_NAME,),
    ).fetchone()
    return text(row["cursor_value"]) if row and row["cursor_value"] else EMPTY_CURSOR


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
) -> None:
    now = now_iso()
    conn.execute(
        """
        UPDATE sync_state
        SET cursor_value = ?,
            last_success_at = ?,
            last_error = '',
            rows_imported = rows_imported + ?,
            updated_at = ?
        WHERE source_name = ?
        """,
        (cursor, now, rows_imported, now, SOURCE_NAME),
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


def sync_status_for_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    init_db(conn)
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
    return {
        "last_attempt": text(status.get("last_attempt_at")),
        "last_success": text(status.get("last_success_at")),
        "total_rows_imported": int(status.get("rows_imported") or 0),
        "raw_snapshot_count": int(status.get("raw_snapshot_count") or 0),
        "open_review_count": int(status.get("unresolved_count") or 0),
        "last_error": text(status.get("last_error")),
    }


def get_last_success_time(config: SyncConfig | None = None) -> str:
    return text(get_sync_status(config).get("last_success_at"))


def get_unresolved_count(config: SyncConfig | None = None) -> int:
    config = config or load_config()
    conn = connect(config.database_path)
    init_db(conn)
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
