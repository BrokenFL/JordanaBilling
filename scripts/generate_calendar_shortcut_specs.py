#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
OUTPUT_DIR = PROJECT_DIR / "data" / "private" / "shortcut-build"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def require(values: dict[str, str], key: str) -> str:
    value = values.get(key)
    if not value:
        raise SystemExit(f"Missing required config value: {key}")
    return value


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def main() -> int:
    values = load_env(ENV_PATH)
    api_key = values.get("JORDANA_PENDING_INGEST_API_KEY") or require(values, "JORDANA_INGEST_API_KEY")
    endpoint = require(values, "JORDANA_APPS_SCRIPT_URL")
    timezone = require(values, "JORDANA_TIMEZONE")

    normal = {
        "artifact_type": "shortcut_payload_spec",
        "shortcut_name": require(values, "JORDANA_NORMAL_SHORTCUT_NAME"),
        "endpoint": endpoint,
        "api_key": api_key,
        "timezone": timezone,
        "payload_version": require(values, "JORDANA_PAYLOAD_VERSION"),
        "calendar_scope": "all_non_all_day_events",
        "preferred_work_calendar": values.get("JORDANA_PREFERRED_WORK_CALENDAR", ""),
        "past": {
            "days": int(require(values, "JORDANA_NORMAL_PAST_DAYS")),
            "capture_window": require(values, "JORDANA_PAST_CAPTURE_WINDOW"),
        },
        "future": {
            "days": int(require(values, "JORDANA_NORMAL_FUTURE_DAYS")),
            "capture_window": require(values, "JORDANA_FUTURE_CAPTURE_WINDOW"),
        },
        "run_complete": {
            "record_type": "run_complete",
            "counts": ["past_found", "past_received", "future_found", "future_received"],
        },
    }
    backfill = {
        "artifact_type": "shortcut_payload_spec",
        "shortcut_name": require(values, "JORDANA_BACKFILL_SHORTCUT_NAME"),
        "endpoint": endpoint,
        "api_key": api_key,
        "timezone": timezone,
        "payload_version": require(values, "JORDANA_PAYLOAD_VERSION"),
        "calendar_scope": "all_non_all_day_events",
        "start": require(values, "JORDANA_BACKFILL_START_DATE"),
        "end": require(values, "JORDANA_BACKFILL_END_DATE"),
        "inclusive_boundaries": True,
        "capture_window": require(values, "JORDANA_BACKFILL_CAPTURE_WINDOW"),
        "recurs": False,
        "run_complete": {
            "record_type": "run_complete",
            "counts": ["past_found", "past_received"],
        },
    }
    manifest = {
        "artifact_type": "shortcut_build_manifest",
        "contains_secret_values": False,
        "live_specs": [
            str(OUTPUT_DIR / "jordana-calendar-snapshot-v2.payload.json"),
            str(OUTPUT_DIR / "jordana-calendar-backfill-2026-06-01-through-2026-06-14.payload.json"),
        ],
        "normal_shortcut_name": normal["shortcut_name"],
        "backfill_shortcut_name": backfill["shortcut_name"],
        "endpoint": "present",
        "api_key": "present",
    }

    write_json(OUTPUT_DIR / "jordana-calendar-snapshot-v2.payload.json", normal)
    write_json(
        OUTPUT_DIR / "jordana-calendar-backfill-2026-06-01-through-2026-06-14.payload.json",
        backfill,
    )
    write_json(OUTPUT_DIR / "manifest.sanitized.json", manifest)
    os.chmod(OUTPUT_DIR, 0o700)
    print(f"Wrote local-only Shortcut specs to {OUTPUT_DIR}")
    print("Secret values were written only to ignored payload specs and were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
