#!/usr/bin/env python3
"""Validate private launcher configuration without exposing private values."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from jordana_invoice.google_sync import load_env_file


REQUIRED_CONFIG = (
    "JORDANA_APPS_SCRIPT_URL",
    "JORDANA_INGEST_API_KEY",
    "JORDANA_DATABASE_PATH",
)


def emit(status: str, message: str) -> int:
    print(f"{status}\t{message}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return emit("INVALID_INVOCATION", "Launcher validation was called incorrectly.")

    project_dir = Path(argv[1]).resolve()
    env_path = Path(argv[2]).resolve()
    if not env_path.is_file():
        return emit("MISSING_CONFIG", "The .env file is missing.")

    try:
        for key in REQUIRED_CONFIG:
            os.environ.pop(key, None)
        load_env_file(env_path)
    except Exception:
        return emit("MISSING_CONFIG", "The .env file could not be read as configuration data.")

    for key in REQUIRED_CONFIG:
        if not os.environ.get(key):
            return emit("MISSING_CONFIG", f"{key} is missing or empty in .env.")

    db_path = Path(os.environ["JORDANA_DATABASE_PATH"]).expanduser()
    if not db_path.is_absolute():
        db_path = project_dir / db_path
    db_path = db_path.resolve()

    if not db_path.is_file():
        return emit(
            "MISSING_DATABASE",
            "The configured SQLite database was not found. Transfer the operational database securely before launching production.",
        )

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                return emit("DATABASE_UNREADABLE", "The configured SQLite database did not pass integrity_check.")
        finally:
            conn.close()
    except sqlite3.Error:
        return emit("DATABASE_UNREADABLE", "The configured SQLite database could not be opened read-only.")

    return emit("OK", f"DB_PATH={db_path}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
