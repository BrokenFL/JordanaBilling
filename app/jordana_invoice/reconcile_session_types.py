"""
Reconciliation script for billing session types.

This script safely updates existing sessions with the new billing session type
fields without modifying approved rates, durations, or finalized invoices.

Usage:
    PYTHONPATH=app python -m jordana_invoice.reconcile_session_types --db <path> [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import connect, migrate_database
from .session_types import (
    map_legacy_to_billing_type,
    map_legacy_to_appointment_method,
    duration_minutes_to_choice,
)
from .util import now_iso


def backup_database(db_path: Path) -> Path:
    """Create a timestamped backup of the database."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.parent / f"{db_path.stem}.backup-reconcile-{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def integrity_check(conn: sqlite3.Connection) -> bool:
    """Run SQLite integrity check."""
    result = conn.execute("PRAGMA integrity_check").fetchone()
    return result[0] == "ok"


def get_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Get counts of sessions by various criteria."""
    counts = {}
    
    counts["total_sessions"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    counts["approved_sessions"] = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE review_status = 'approved'"
    ).fetchone()[0]
    counts["unapproved_sessions"] = counts["total_sessions"] - counts["approved_sessions"]
    
    counts["finalized_invoices"] = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE status = 'finalized'"
    ).fetchone()[0]
    counts["finalized_line_items"] = conn.execute(
        "SELECT COUNT(*) FROM invoice_line_items WHERE invoice_id IN (SELECT invoice_id FROM invoices WHERE status = 'finalized')"
    ).fetchone()[0]
    
    counts["sessions_with_billing_type"] = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE billing_session_type IS NOT NULL"
    ).fetchone()[0]
    counts["sessions_without_billing_type"] = counts["total_sessions"] - counts["sessions_with_billing_type"]
    
    return counts


def get_billing_type_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    """Get distribution of billing session types."""
    rows = conn.execute("""
        SELECT COALESCE(billing_session_type, 'NULL') AS btype, COUNT(*) AS cnt
        FROM sessions
        GROUP BY billing_session_type
    """).fetchall()
    return {row[0]: row[1] for row in rows}


def get_legacy_rate_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Find rate rules scoped to legacy service modes."""
    rows = conn.execute("""
        SELECT rate_rule_id, service_mode, rate_group, duration_minutes, 
               time_category, amount_cents, effective_from, active
        FROM rate_rules
        WHERE service_mode IN ('office', 'phone', 'facetime')
           OR rate_group IN ('office', 'remote')
    """).fetchall()
    return [dict(row) for row in rows]


def reconcile_unapproved_sessions(
    conn: sqlite3.Connection,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Reconcile unapproved sessions with new billing session type fields.
    
    For unapproved sessions:
    - Populate billing_session_type using priority rules
    - Populate appointment_method from service_mode
    - Calculate duration_choice from duration_minutes
    - Preserve all raw evidence
    - Do not auto-approve
    - Do not change rates
    """
    stats = {
        "processed": 0,
        "updated": 0,
        "skipped_approved": 0,
        "by_billing_type": {},
        "duration_defaulted": 0,
        "custom_duration": 0,
        "house_call_suggested": 0,
        "legacy_service_modes": {},
    }
    
    rows = conn.execute("""
        SELECT s.id, s.service_mode, s.is_weekend, s.is_evening, 
               s.duration_minutes, s.review_status, s.billing_session_type,
               c.location AS location_text
        FROM sessions s
        LEFT JOIN calendar_event_candidates c ON c.id = s.candidate_id
        WHERE s.review_status != 'approved'
    """).fetchall()
    
    now = now_iso()
    
    for row in rows:
        stats["processed"] += 1
        session_id = row["id"]
        service_mode = row["service_mode"]
        is_weekend = bool(row["is_weekend"])
        is_evening = bool(row["is_evening"])
        duration_minutes = row["duration_minutes"]
        location_text = row["location_text"]
        existing_billing_type = row["billing_session_type"]
        
        if service_mode:
            stats["legacy_service_modes"][service_mode] = stats["legacy_service_modes"].get(service_mode, 0) + 1
        
        billing_type, billing_source, house_call_suggested = map_legacy_to_billing_type(
            service_mode, is_weekend, is_evening, location_text
        )
        appointment_method = map_legacy_to_appointment_method(service_mode)
        duration_choice, custom_minutes = duration_minutes_to_choice(duration_minutes)
        
        stats["by_billing_type"][billing_type] = stats["by_billing_type"].get(billing_type, 0) + 1
        
        if duration_minutes is None:
            stats["duration_defaulted"] += 1
        if duration_choice == "custom":
            stats["custom_duration"] += 1
        if house_call_suggested:
            stats["house_call_suggested"] += 1
        
        if existing_billing_type is not None:
            continue
        
        if not dry_run:
            conn.execute("""
                UPDATE sessions
                SET billing_session_type = ?,
                    appointment_method = ?,
                    duration_choice = ?,
                    custom_duration_minutes = ?,
                    house_call_suggested = ?,
                    billing_type_source = ?,
                    location_text = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                billing_type,
                appointment_method,
                duration_choice,
                custom_minutes,
                1 if house_call_suggested else 0,
                billing_source,
                location_text,
                now,
                session_id,
            ))
        
        stats["updated"] += 1
    
    if not dry_run:
        conn.commit()
    
    return stats


def reconcile_approved_sessions(
    conn: sqlite3.Connection,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    For approved sessions, only populate billing_session_type if missing.
    Do NOT change:
    - approved_rate_cents
    - approved_duration_minutes
    - participants
    - billing_party_id
    """
    stats = {
        "processed": 0,
        "updated": 0,
        "preserved": 0,
        "legacy_values": [],
    }
    
    rows = conn.execute("""
        SELECT s.id, s.service_mode, s.is_weekend, s.is_evening,
               s.billing_session_type, s.approved_rate_cents,
               c.location AS location_text
        FROM sessions s
        LEFT JOIN calendar_event_candidates c ON c.id = s.candidate_id
        WHERE s.review_status = 'approved'
    """).fetchall()
    
    now = now_iso()
    
    for row in rows:
        stats["processed"] += 1
        session_id = row["id"]
        existing_billing_type = row["billing_session_type"]
        service_mode = row["service_mode"]
        
        if existing_billing_type is not None:
            stats["preserved"] += 1
            continue
        
        if service_mode in ("office", "phone", "facetime", "house_call", "unknown"):
            stats["legacy_values"].append({
                "session_id": session_id,
                "service_mode": service_mode,
                "approved_rate_cents": row["approved_rate_cents"],
            })
        
        billing_type, billing_source, house_call_suggested = map_legacy_to_billing_type(
            service_mode, bool(row["is_weekend"]), bool(row["is_evening"]), row["location_text"]
        )
        appointment_method = map_legacy_to_appointment_method(service_mode)
        
        if not dry_run:
            conn.execute("""
                UPDATE sessions
                SET billing_session_type = ?,
                    appointment_method = ?,
                    billing_type_source = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                billing_type,
                appointment_method,
                "reconciled_from_legacy",
                now,
                session_id,
            ))
        
        stats["updated"] += 1
    
    if not dry_run:
        conn.commit()
    
    return stats


def generate_report(
    before_counts: dict[str, int],
    after_counts: dict[str, int],
    unapproved_stats: dict[str, Any],
    approved_stats: dict[str, Any],
    legacy_rules: list[dict[str, Any]],
    dry_run: bool,
) -> str:
    """Generate a reconciliation report."""
    lines = [
        "# Billing Session Type Reconciliation Report",
        f"Generated: {datetime.now().isoformat()}",
        f"Mode: {'DRY RUN' if dry_run else 'LIVE'}",
        "",
        "## Before Reconciliation",
        f"- Total sessions: {before_counts['total_sessions']}",
        f"- Approved sessions: {before_counts['approved_sessions']}",
        f"- Unapproved sessions: {before_counts['unapproved_sessions']}",
        f"- Sessions with billing_session_type: {before_counts['sessions_with_billing_type']}",
        f"- Sessions without billing_session_type: {before_counts['sessions_without_billing_type']}",
        f"- Finalized invoices: {before_counts['finalized_invoices']}",
        f"- Finalized line items: {before_counts['finalized_line_items']}",
        "",
        "## Unapproved Sessions Reconciliation",
        f"- Processed: {unapproved_stats['processed']}",
        f"- Updated: {unapproved_stats['updated']}",
        f"- Duration defaulted to 60: {unapproved_stats['duration_defaulted']}",
        f"- Custom duration: {unapproved_stats['custom_duration']}",
        f"- House call suggested: {unapproved_stats['house_call_suggested']}",
        "",
        "### By Billing Session Type",
    ]
    
    for btype, count in sorted(unapproved_stats["by_billing_type"].items()):
        lines.append(f"- {btype}: {count}")
    
    lines.extend([
        "",
        "### Legacy Service Modes Found",
    ])
    for mode, count in sorted(unapproved_stats["legacy_service_modes"].items()):
        lines.append(f"- {mode}: {count}")
    
    lines.extend([
        "",
        "## Approved Sessions Reconciliation",
        f"- Processed: {approved_stats['processed']}",
        f"- Updated (billing_type only): {approved_stats['updated']}",
        f"- Preserved (already had billing_type): {approved_stats['preserved']}",
        f"- Legacy values for human review: {len(approved_stats['legacy_values'])}",
        "",
    ])
    
    if approved_stats["legacy_values"]:
        lines.append("### Legacy Values Requiring Review")
        for item in approved_stats["legacy_values"][:20]:
            lines.append(f"- Session {item['session_id']}: {item['service_mode']} (rate: {item['approved_rate_cents']})")
        if len(approved_stats["legacy_values"]) > 20:
            lines.append(f"... and {len(approved_stats['legacy_values']) - 20} more")
    
    lines.extend([
        "",
        "## Legacy Rate Rules",
        f"Found {len(legacy_rules)} rate rules scoped to Office/Phone/FaceTime:",
    ])
    for rule in legacy_rules[:10]:
        lines.append(f"- Rule {rule['rate_rule_id']}: {rule['service_mode'] or rule['rate_group']} ${rule['amount_cents']/100:.2f}")
    if len(legacy_rules) > 10:
        lines.append(f"... and {len(legacy_rules) - 10} more")
    
    lines.extend([
        "",
        "## After Reconciliation",
        f"- Sessions with billing_session_type: {after_counts.get('sessions_with_billing_type', 'N/A')}",
        "",
        "## Finalized Invoices",
        "No changes made to finalized invoices or line items.",
        "",
        "## Notes",
        "- Approved session rates, durations, and participants were NOT modified",
        "- Finalized invoice snapshots were NOT modified",
        "- This reconciliation is idempotent",
    ])
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Reconcile billing session types")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--report", help="Path to save reconciliation report")
    args = parser.parse_args()
    
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1
    
    if not args.dry_run:
        backup_path = backup_database(db_path)
        print(f"Created backup: {backup_path}")
    
    migrate_database(db_path)
    conn = connect(db_path)
    
    if not integrity_check(conn):
        print("ERROR: Database integrity check failed!")
        return 1
    print("Database integrity check: OK")
    
    before_counts = get_counts(conn)
    print(f"Before: {before_counts['total_sessions']} sessions, {before_counts['sessions_without_billing_type']} need billing_type")
    
    legacy_rules = get_legacy_rate_rules(conn)
    print(f"Found {len(legacy_rules)} legacy rate rules")
    
    print("\nReconciling unapproved sessions...")
    unapproved_stats = reconcile_unapproved_sessions(conn, dry_run=args.dry_run)
    print(f"  Processed: {unapproved_stats['processed']}, Updated: {unapproved_stats['updated']}")
    
    print("\nReconciling approved sessions...")
    approved_stats = reconcile_approved_sessions(conn, dry_run=args.dry_run)
    print(f"  Processed: {approved_stats['processed']}, Updated: {approved_stats['updated']}, Preserved: {approved_stats['preserved']}")
    
    after_counts = get_counts(conn) if not args.dry_run else {}
    
    if not integrity_check(conn):
        print("ERROR: Post-reconciliation integrity check failed!")
        return 1
    print("\nPost-reconciliation integrity check: OK")
    
    report = generate_report(
        before_counts, after_counts,
        unapproved_stats, approved_stats,
        legacy_rules, args.dry_run
    )
    
    if args.report:
        Path(args.report).write_text(report)
        print(f"\nReport saved to: {args.report}")
    else:
        print("\n" + report)
    
    conn.close()
    return 0


if __name__ == "__main__":
    exit(main())
