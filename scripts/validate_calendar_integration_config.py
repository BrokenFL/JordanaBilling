#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"

REQUIRED_ACTIVE = (
    "JORDANA_APPS_SCRIPT_URL",
    "JORDANA_INGEST_API_KEY",
    "JORDANA_DATABASE_PATH",
)
REQUIRED_ADMIN = (
    "JORDANA_TIMEZONE",
    "JORDANA_RAW_SHEET",
    "JORDANA_RUN_LOG_SHEET",
    "JORDANA_NORMAL_PAST_DAYS",
    "JORDANA_NORMAL_FUTURE_DAYS",
    "JORDANA_PAST_CAPTURE_WINDOW",
    "JORDANA_FUTURE_CAPTURE_WINDOW",
    "JORDANA_BACKFILL_START_DATE",
    "JORDANA_BACKFILL_END_DATE",
    "JORDANA_BACKFILL_CAPTURE_WINDOW",
    "JORDANA_NORMAL_SHORTCUT_NAME",
    "JORDANA_BACKFILL_SHORTCUT_NAME",
    "JORDANA_PAYLOAD_VERSION",
)
OPTIONAL_ADMIN = (
    "JORDANA_PENDING_INGEST_API_KEY",
    "JORDANA_SPREADSHEET_ID",
    "JORDANA_APPS_SCRIPT_PROJECT_ID",
    "JORDANA_APPS_SCRIPT_DEPLOYMENT_ID",
)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def status(value: str | None) -> str:
    if value:
        return "present"
    return "missing"


def main() -> int:
    values = load_env(ENV_PATH)
    if not values:
        print(f"Missing config file: {ENV_PATH}", file=sys.stderr)
        return 1

    missing_active = [key for key in REQUIRED_ACTIVE if not values.get(key)]
    missing_admin = [key for key in REQUIRED_ADMIN if not values.get(key)]

    print("Calendar integration config check")
    print(f"env_file=present path={ENV_PATH}")
    for key in REQUIRED_ACTIVE + REQUIRED_ADMIN + OPTIONAL_ADMIN:
        print(f"{key}={status(values.get(key))}")

    if missing_active or missing_admin:
        print("Config check failed: required values are missing.", file=sys.stderr)
        return 1
    print("Config check passed. Secret values were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
