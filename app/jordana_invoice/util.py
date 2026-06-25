from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


PAYMENT_STATUS_UNPAID = "unpaid"
PAYMENT_STATUS_PAID_AT_SESSION = "paid_at_session"

_LEGACY_PAYMENT_MAP = {
    "unpaid": "unpaid",
    "paid_at_session": "paid_at_session",
    "paid": "paid_at_session",
    "unresolved": "unpaid",
    "partially_paid": "unpaid",
    "waived": "unpaid",
    "not_billable": "unpaid",
    "": "unpaid",
}


def normalize_payment_status(value: Any) -> str:
    raw = text(value)
    return _LEGACY_PAYMENT_MAP.get(raw, "unpaid")


_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@")


def _is_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def csv_safe(value: Any) -> str:
    """Neutralise CSV formula injection.

    If *value* (after stripping leading whitespace) begins with ``=``,
    ``+``, ``-``, or ``@``, prefix it with a single apostrophe so that
    spreadsheet applications treat it as text rather than a formula.

    Genuine numeric values (including negative integers and decimals)
    are returned unchanged.
    """
    if value is None:
        return ""
    s = str(value)
    if not s or s[0].isspace():
        stripped = s.lstrip()
        if not stripped:
            return s
        if stripped[0] in _CSV_INJECTION_PREFIXES and not _is_numeric(stripped):
            return "'" + s
    elif s[0] in _CSV_INJECTION_PREFIXES and not _is_numeric(s):
        return "'" + s
    return s
