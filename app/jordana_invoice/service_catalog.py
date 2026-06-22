from __future__ import annotations

import re
import sqlite3
from typing import Any

from .util import new_id, now_iso


def normalize_service_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def list_services(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE active = 1"
    rows = conn.execute(
        f"SELECT * FROM service_catalog {where} ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    return [dict(row) for row in rows]


def learn_service(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    increment_usage: bool = True,
) -> dict[str, Any]:
    display = " ".join(display_name.strip().split())
    normalized = normalize_service_name(display)
    if not normalized:
        raise ValueError("Service name is required.")
    existing = conn.execute(
        "SELECT * FROM service_catalog WHERE normalized_name = ?", (normalized,)
    ).fetchone()
    now = now_iso()
    if existing:
        if increment_usage:
            conn.execute(
                """
                UPDATE service_catalog
                SET usage_count = usage_count + 1,
                    first_used_at = COALESCE(first_used_at, ?),
                    last_used_at = ?, updated_at = ?
                WHERE service_catalog_id = ?
                """,
                (now, now, now, existing["service_catalog_id"]),
            )
        return dict(
            conn.execute(
                "SELECT * FROM service_catalog WHERE service_catalog_id = ?",
                (existing["service_catalog_id"],),
            ).fetchone()
        )
    service_id = new_id()
    conn.execute(
        """
        INSERT INTO service_catalog (
          service_catalog_id, canonical_name, normalized_name, display_name,
          active, usage_count, first_used_at, last_used_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
        """,
        (
            service_id,
            normalized,
            normalized,
            display,
            1 if increment_usage else 0,
            now if increment_usage else None,
            now if increment_usage else None,
            now,
            now,
        ),
    )
    conn.execute(
        "INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at) VALUES (?, 'service_catalog', ?, 'created', ?, ?)",
        (new_id(), service_id, '{"source":"review_or_invoice"}', now),
    )
    return dict(conn.execute("SELECT * FROM service_catalog WHERE service_catalog_id = ?", (service_id,)).fetchone())


def set_service_active(conn: sqlite3.Connection, service_id: str, active: bool) -> dict[str, Any]:
    now = now_iso()
    cursor = conn.execute(
        "UPDATE service_catalog SET active = ?, updated_at = ? WHERE service_catalog_id = ?",
        (1 if active else 0, now, service_id),
    )
    if not cursor.rowcount:
        raise ValueError("Service was not found.")
    conn.execute(
        "INSERT INTO audit_log (id, entity_type, entity_id, action, details, created_at) VALUES (?, 'service_catalog', ?, ?, '{}', ?)",
        (new_id(), service_id, "reactivated" if active else "deactivated", now),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM service_catalog WHERE service_catalog_id = ?", (service_id,)).fetchone())
