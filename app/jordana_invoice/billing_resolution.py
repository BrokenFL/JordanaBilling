"""Resolve retired bill-to references from established UUID relationships."""
from .calendar_identity import utc_datetime


def replacement_for_inactive_payer(conn, session, participants, *, configured_before=None):
    payer = conn.execute("SELECT active FROM billing_parties WHERE billing_party_id=?",
                         (session["billing_party_id"],)).fetchone()
    # Never replace a deliberately selected active payer, or invent a missing one.
    if not payer or payer["active"]:
        return None
    people = {p.get("person_id") for p in participants}
    if not session["session_date"] or not people or any(not person for person in people) or any(p.get("is_proposed") for p in participants):
        return None
    marks = ",".join("?" for _ in people)
    rows = conn.execute(f"""
        SELECT ca.account_id, ca.default_billing_party_id AS billing_party_id,
               ca.created_at, am.created_at AS member_created_at, am.person_id
        FROM client_accounts ca
        JOIN billing_parties bp ON bp.billing_party_id=ca.default_billing_party_id
        JOIN account_members am ON am.account_id=ca.account_id
        WHERE ca.active=1 AND bp.active=1 AND am.person_id IN ({marks})
          AND (am.effective_from IS NULL OR am.effective_from <= ?)
          AND (am.effective_through IS NULL OR am.effective_through >= ?)
    """, (*people, session["session_date"], session["session_date"])).fetchall()
    cutoff = utc_datetime(configured_before) if configured_before else None
    if configured_before and cutoff is None:
        return None
    groups = {}
    for row in rows:
        if cutoff:
            timestamps = [utc_datetime(row[k]) for k in ("created_at", "member_created_at")]
            if any(t is None or t > cutoff for t in timestamps):
                continue
        key = (row["account_id"], row["billing_party_id"])
        groups.setdefault(key, set()).add(row["person_id"])
    matches = [key for key, covered in groups.items() if covered == people]
    if len(matches) != 1:
        return None
    account_id, billing_party_id = matches[0]
    return {"account_id": account_id, "billing_party_id": billing_party_id}
