"""Read-only CLI for the paid-at-session backfill dry-run analyzer.

Usage:
    python -m jordana_invoice.payment_backfill_cli --dry-run --db /path/to/database.sqlite

This command opens the database in strict read-only mode, runs the
existing ``dry_run_paid_at_session_backfill`` analyzer, and prints the
sanitized aggregate report as JSON.  No payments, allocations, sessions,
invoices, audits, or reports are created or modified.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .payment_services import dry_run_paid_at_session_backfill

EXIT_OK = 0
EXIT_OTHER = 1
EXIT_ARGS = 2
EXIT_SCHEMA = 3

SAFETY_MESSAGE = "READ-ONLY DRY RUN: no payments, allocations, sessions, invoices, audits, or reports were changed."


def _validate_db_path(raw: str) -> Path:
    if not raw or not raw.strip():
        print("Error: --db path is required.", file=sys.stderr)
        sys.exit(EXIT_ARGS)
    resolved = Path(raw).resolve()
    if not resolved.exists():
        print(f"Error: database path does not exist: {resolved}", file=sys.stderr)
        sys.exit(EXIT_ARGS)
    if not resolved.is_file():
        print(f"Error: database path is not a regular file: {resolved}", file=sys.stderr)
        sys.exit(EXIT_ARGS)
    if resolved.stat().st_size == 0:
        print(f"Error: database file is empty: {resolved}", file=sys.stderr)
        sys.exit(EXIT_ARGS)
    return resolved


def _open_readonly(resolved: Path) -> sqlite3.Connection:
    uri = f"file:{resolved}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.OperationalError:
        print("Error: cannot open database in read-only mode.", file=sys.stderr)
        sys.exit(EXIT_SCHEMA)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.OperationalError:
        conn.close()
        print("Error: cannot configure read-only pragmas.", file=sys.stderr)
        sys.exit(EXIT_SCHEMA)
    return conn


def _check_schema(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute(
            "SELECT migration_id FROM schema_migrations WHERE migration_id = '004_payment_provenance'"
        ).fetchone()
    except sqlite3.OperationalError:
        print(
            "Error: database lacks the required payment-provenance schema (migration 004). "
            "Migrate a copied database through the normal supported process first.",
            file=sys.stderr,
        )
        sys.exit(EXIT_SCHEMA)
    if row is None:
        print(
            "Error: database has not applied migration 004_payment_provenance. "
            "Migrate a copied database through the normal supported process first.",
            file=sys.stderr,
        )
        sys.exit(EXIT_SCHEMA)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jordana_invoice.payment_backfill_cli",
        description="Read-only dry-run analyzer for paid-at-session payment backfill.",
    )
    parser.add_argument("--dry-run", action="store_true", required=True,
                        help="Run the read-only dry-run analysis (required).")
    parser.add_argument("--db", required=True,
                        help="Path to the SQLite database file (required).")
    args = parser.parse_args(argv)

    resolved = _validate_db_path(args.db)
    conn = _open_readonly(resolved)
    try:
        _check_schema(conn)
        report = dry_run_paid_at_session_backfill(conn)
    except SystemExit:
        raise
    except Exception:
        print("Error: analysis failed. See stderr for details.", file=sys.stderr)
        conn.close()
        return EXIT_OTHER
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print(json.dumps(report, indent=2, sort_keys=True))
    print(SAFETY_MESSAGE)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
