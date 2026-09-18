from __future__ import annotations

import re
from typing import Any


_DR_TITLE_RE = re.compile(r"^dr\.?\s+", re.IGNORECASE)


def format_invoice_person_name(display_name: Any, use_dr_on_invoices: Any = False) -> str:
    """Return the intentionally selected invoice-facing person name.

    Calendar identity and the person's normal display name remain unchanged.
    The title is added only when the stored client preference is enabled.
    """
    name = " ".join(str(display_name or "").split())
    if not name or not bool(use_dr_on_invoices) or _DR_TITLE_RE.match(name):
        return name
    return f"Dr. {name}"
