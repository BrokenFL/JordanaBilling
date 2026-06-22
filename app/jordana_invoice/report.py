from __future__ import annotations

import sqlite3


def acceptance_report(conn: sqlite3.Connection, import_run_id: str) -> str:
    sections = [
        ("Likely client sessions", "client_session"),
        ("Likely personal/admin/nonbillable events", None),
        ("Requires Jojo review", "review"),
    ]
    lines = [f"# Acceptance Report", "", f"Import run: `{import_run_id}`", ""]

    for title, classification in sections:
        lines.extend([f"## {title}", ""])
        rows = query_section(conn, import_run_id, classification)
        if not rows:
            lines.extend(["No records.", ""])
            continue
        for row in rows:
            fields = row["fields_requiring_review"] or "[]"
            proposed = row["proposed_client_name"] or ""
            start = row["proposed_start_at"] or row["start_at"] or ""
            duration = row["proposed_duration_minutes"] or row["calendar_duration_minutes"] or ""
            lines.append(
                "- "
                f"{row['classification']} "
                f"(confidence {float(row['confidence']):.2f}) | "
                f"client candidate: {proposed or 'none'} | "
                f"time: {start or 'unknown'} | "
                f"duration: {duration or 'unknown'} | "
                f"review: {fields} | "
                f"reason: {row['explanation']}"
            )
        lines.append("")

    lines.extend(
        [
            "## Invoice status",
            "",
            "No invoices generated. Phase 1 only imports, parses, classifies, and queues review.",
            "",
        ]
    )
    return "\n".join(lines)


def query_section(
    conn: sqlite3.Connection,
    import_run_id: str,
    classification: str | None,
) -> list[sqlite3.Row]:
    if classification == "review":
        return conn.execute(
            """
            SELECT c.*
            FROM calendar_event_candidates c
            JOIN review_queue q ON q.candidate_id = c.id
            WHERE c.import_run_id = ? AND q.status = 'open'
            ORDER BY q.priority, c.start_at, c.title
            """,
            (import_run_id,),
        ).fetchall()
    if classification is None:
        return conn.execute(
            """
            SELECT *
            FROM calendar_event_candidates
            WHERE import_run_id = ?
              AND classification IN (
                'administrative', 'personal', 'cancelled',
                'no_show', 'nonbillable'
              )
            ORDER BY start_at, title
            """,
            (import_run_id,),
        ).fetchall()
    return conn.execute(
        """
        SELECT *
        FROM calendar_event_candidates
        WHERE import_run_id = ? AND classification = ?
        ORDER BY start_at, title
        """,
        (import_run_id, classification),
    ).fetchall()
