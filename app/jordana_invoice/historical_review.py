"""Reversible Review eligibility derived from preserved historical captures.

Raw rows and financial history are never rewritten. Absence is considered only
inside a successfully imported historical capture, never in a future window or
because a record aged out of the rolling window.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import timedelta

from .calendar_identity import canonical_structural_parts, utc_datetime
from .capture_windows import is_past_capture_window, is_future_capture_window
from .util import json_dumps, now_iso, text, new_id

MANUAL_MARKS = {"marked_personal", "marked_administrative", "marked_nonbillable", "marked_duplicate"}
DECISIONS = MANUAL_MARKS | {"restored_to_review_queue", "marked_client_session", "sent_to_review"}


def manually_excluded(conn: sqlite3.Connection, candidate_id: str) -> bool:
    marks = ",".join("?" for _ in DECISIONS)
    row = conn.execute(
        f"SELECT action FROM audit_log WHERE entity_id = ? AND action IN ({marks}) ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (candidate_id, *DECISIONS),
    ).fetchone()
    return bool(row and row["action"] in MANUAL_MARKS)


def latest_manual_decisions(conn: sqlite3.Connection) -> dict[str, str]:
    """Read decision history once for a reconciliation batch, newest first.

    Equivalent to manually_excluded for each entity, including same-time ties.
    Avoid repeatedly scanning the entire audit log for every appointment.
    """
    marks = ",".join("?" for _ in DECISIONS)
    decisions = {}
    for row in conn.execute(
        f"SELECT entity_id, action FROM audit_log WHERE action IN ({marks}) ORDER BY created_at DESC, rowid DESC",
        tuple(DECISIONS),
    ):
        decisions.setdefault(row["entity_id"], row["action"])
    return decisions


def historical_evidence(row):
    if is_future_capture_window(row["capture_window"]):
        return False
    if is_past_capture_window(row["capture_window"]):
        captured, end = utc_datetime(row["captured_at"]), utc_datetime(row["end_at"])
        return bool(captured and end and captured >= end)
    # Preserve unlabelled historical CSV recovery compatibility.
    return str(row["payload_version"] or "0") in {"0", "1", "2", ""}


def _identity(row):
    # Duration is already represented by start/end; payloads may encode numeric
    # minutes differently. Title changes remain distinct, not fuzzy matches.
    return canonical_structural_parts(row, include_duration=False)


def _coverage(batch, proof):
    """Return bounded coverage; incomplete captures never establish absence.

    Legacy exports lack Run_Log. Their observed span is a conservative fallback:
    do not assume the boundary dates were fully captured. In particular a
    fixed-date Shortcut whose date picker truncates a boundary cannot erase
    positive observations outside the range it demonstrably returned.
    """
    captured = {utc_datetime(r["captured_at"]) for r in batch}
    if len(captured) != 1 or None in captured:
        return None
    captured_at = next(iter(captured))
    starts = [utc_datetime(r["start_at"]) for r in batch]
    ends = [utc_datetime(r["end_at"]) for r in batch]
    if None in starts or None in ends:
        return None
    if proof and (proof["status"] != "complete" or proof["past_found"] != proof["past_received"]):
        return None
    # Require the complete locally received batch when counts are supplied.
    if proof and proof["past_received"] != len(batch):
        return None
    raw = json.loads(batch[0]["raw_json"] or "{}")
    start, end = utc_datetime(raw.get("window_start")), utc_datetime(raw.get("window_end"))
    if start is None or end is None:
        days = {"past_3_days": 3, "past_7_days": 7}.get(batch[0]["capture_window"])
        if days:
            start, end = captured_at - timedelta(days=days), captured_at
        elif batch[0]["capture_window"] == "backfill_2026_06_01_through_2026_06_14":
            start = utc_datetime("2026-06-01T00:00:00-04:00")
            end = utc_datetime("2026-06-14T23:59:59-04:00")
        else:
            return None
    # Explicit-date captures are conservatively bounded even with run counts:
    # equal found/received counts prove transfer, not the date picker's scope.
    explicit = bool(raw.get("window_start") or raw.get("window_end"))
    if not proof or explicit:
        start, end = max(start, min(starts)), min(end, max(ends))
    return (start, min(end, captured_at), captured_at) if start <= end else None


def reconcile_historical_review(conn: sqlite3.Connection) -> int:
    from .importer import candidate_key
    from .parser import parse_event
    from .review_services import _auto_link_exact_name_participants, refresh_candidate_suggestions

    rows = conn.execute("SELECT r.*, i.status import_status FROM raw_calendar_snapshots r JOIN import_runs i ON i.id=r.import_run_id").fetchall()
    by_id = {r["id"]: r for r in rows}
    by_key = defaultdict(list)
    batches = defaultdict(list)
    for row in rows:
        by_key[candidate_key(row)].append(row)
        if is_past_capture_window(row["capture_window"]) and row["import_status"] == "imported":
            batches[(row["run_id"], row["capture_window"])].append(row)
    proofs = {r["run_id"]: r for r in conn.execute("SELECT * FROM calendar_capture_runs")}
    covering = []
    for (run, _), batch in batches.items():
        try:
            bounds = _coverage(batch, proofs.get(run))
        except (ValueError, TypeError, AttributeError):
            bounds = None
        if bounds:
            covering.append((*bounds, {_identity(r) for r in batch if historical_evidence(r)}, run))
    # A completed normal zero-event run has no raw rows, but its Run_Log counts
    # still prove an empty past window. Never infer fixed backfill bounds here.
    for proof in proofs.values():
        captured = utc_datetime(proof["started_at"])
        if (captured and proof["status"] == "complete" and not proof["past_found"]
                and not proof["past_received"] and not proof["error_message"]
                and text(proof["batch_name"]).startswith("JORDANA_CALENDAR_")):
            covering.append((captured - timedelta(days=3), captured, captured, set(), proof["run_id"]))
    # Newest capture first. A newer window that does not cover a record is irrelevant.
    covering.sort(key=lambda b: b[2], reverse=True)
    aliases = defaultdict(set)
    for r in conn.execute("SELECT candidate_id, source_raw_snapshot_id FROM candidate_identity_aliases"):
        aliases[r["candidate_id"]].add(r["source_raw_snapshot_id"])
    protected = {r[0] for r in conn.execute("SELECT source_session_id FROM invoice_line_items WHERE source_session_id IS NOT NULL UNION SELECT session_id FROM payment_allocations UNION SELECT source_session_id FROM payments WHERE source_session_id IS NOT NULL")}
    changed = 0
    eligible = []
    manual_decisions = latest_manual_decisions(conn)
    for c in conn.execute("SELECT * FROM calendar_event_candidates").fetchall():
        session = conn.execute("SELECT * FROM sessions WHERE candidate_id=?", (c["id"],)).fetchone()
        if c["review_status"] == "approved" or (session and (session["review_status"] == "approved" or session["id"] in protected)):
            continue
        linked = {r["id"]: r for r in by_key[c["candidate_key"]]}
        for rid in aliases[c["id"]] | {c["latest_raw_snapshot_id"]}:
            if rid in by_id:
                linked[rid] = by_id[rid]
        if not linked:
            continue  # Manually entered or fixture records have no capture evidence.
        positive = [r for r in linked.values() if historical_evidence(r)]
        manual = manual_decisions.get(c["id"]) in MANUAL_MARKS
        state = "manual_exclusion" if manual else "eligible" if positive else "future_only"
        if positive and not manual:
            latest = max(positive, key=lambda r: (utc_datetime(r["captured_at"]) or utc_datetime("1900-01-01T00:00:00Z"), text(r["ingested_at"])))
            start, end, captured = utc_datetime(latest["start_at"]), utc_datetime(latest["end_at"]), utc_datetime(latest["captured_at"])
            if start and end and captured:
                for lower, upper, observed, identities, run in covering:
                    if lower <= start and end <= upper and observed >= captured:
                        # Multiple batches at the same instant may overlap.
                        same = [b for b in covering if b[2] == observed and b[0] <= start and end <= b[1]]
                        if not any(_identity(latest) in b[3] for b in same):
                            state = "absent"
                        break
        if state == "eligible" and session and session["review_status"] != "excluded":
            # Recognizable personal/admin titles do not need a billing review.
            # Confirmed people take precedence over a keyword interpretation.
            confirmed = conn.execute("SELECT 1 FROM session_participants WHERE session_id=? AND person_id IS NOT NULL", (session["id"],)).fetchone()
            parsed = parse_event(dict(latest))
            if parsed.classification in {"personal", "administrative", "nonbillable"} and not confirmed:
                state = "nonclient"
            elif (parsed.classification == "client_session" and not session["approved_duration_minutes"]
                  and parsed.proposed_duration_minutes and session["duration_minutes"] != parsed.proposed_duration_minutes):
                conn.execute("UPDATE sessions SET duration_minutes=?, parsed_duration_minutes=?, end_at=? WHERE id=?", (parsed.proposed_duration_minutes, parsed.proposed_duration_minutes, parsed.proposed_end_at, session["id"]))
                conn.execute("INSERT INTO audit_log (id,entity_type,entity_id,action,details,created_at) VALUES (?, 'session', ?, 'historical_duration_refreshed', ?, ?)", (new_id(), session["id"], json_dumps({"old_minutes": session["duration_minutes"], "new_minutes": parsed.proposed_duration_minutes}), now_iso()))
        if state != c["calendar_review_state"]:
            conn.execute("UPDATE calendar_event_candidates SET calendar_review_state=? WHERE id=?", (state, c["id"]))
            conn.execute("INSERT INTO audit_log (id,entity_type,entity_id,action,details,created_at) VALUES (?, 'calendar_event_candidate', ?, 'historical_review_eligibility', ?, ?)", (new_id(), c["id"], json_dumps({"old_state": c["calendar_review_state"], "new_state": state}), now_iso()))
            changed += 1
        if manual:
            # Repair earlier imports that reopened a recorded human exclusion.
            conn.execute("UPDATE calendar_event_candidates SET review_status='excluded' WHERE id=? AND review_status!='excluded'", (c["id"],))
            if session:
                conn.execute("UPDATE sessions SET review_status='excluded' WHERE id=? AND review_status!='excluded'", (session["id"],))
        elif state == "eligible" and session and session["review_status"] != "excluded":
            eligible.append(c["id"])
    _auto_link_exact_name_participants(conn)
    for cid in eligible:
        refresh_candidate_suggestions(conn, cid, record_review_event=False)
    return changed
