#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    values = load_env(ENV_PATH)
    missing = [
        key
        for key in ("JORDANA_PENDING_INGEST_API_KEY", "JORDANA_SPREADSHEET_ID")
        if not values.get(key)
    ]
    if missing:
        print("Cannot configure Apps Script automatically yet.")
        print("Missing local admin values: " + ", ".join(missing))
        print("No secret values were printed.")
        return 2

    if not shutil.which("clasp"):
        print("Cannot configure Apps Script automatically: clasp is not installed.")
        print("Manual Script Properties to set in the existing Apps Script project:")
        print("  INGEST_API_KEY = value from JORDANA_PENDING_INGEST_API_KEY")
        print("  JORDANA_SPREADSHEET_ID = value from JORDANA_SPREADSHEET_ID")
        print("Then deploy integrations/apps_script/Code.gs to the existing web-app deployment.")
        print("No secret values were printed.")
        return 2

    print("clasp is installed, but this repo has no .clasp.json/scriptId.")
    print("Preserve the existing Apps Script project; add .clasp.json locally only, then run:")
    print("  clasp push")
    print("  clasp deploy --deploymentId <existing deployment id>")
    print("Set Script Properties in the Apps Script editor or with an authenticated admin command.")
    print("No secret values were printed.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
