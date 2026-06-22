from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS import_runs (
  id TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_path TEXT,
  imported_at TEXT NOT NULL,
  source_row_count INTEGER NOT NULL DEFAULT 0,
  completed_run_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS raw_calendar_snapshots (
  id TEXT PRIMARY KEY,
  import_run_id TEXT NOT NULL REFERENCES import_runs(id),
  source_row_number INTEGER NOT NULL,
  source_hash TEXT NOT NULL,
  snapshot_key TEXT,
  run_id TEXT,
  batch_name TEXT,
  capture_window TEXT,
  captured_at TEXT,
  ingested_at TEXT,
  source_device TEXT,
  timezone TEXT,
  calendar_event_id TEXT,
  event_fingerprint TEXT,
  event_title TEXT,
  start_at TEXT,
  end_at TEXT,
  duration_minutes INTEGER,
  location TEXT,
  notes TEXT,
  calendar_name TEXT,
  payload_version INTEGER,
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_calendar_snapshots_source_hash
  ON raw_calendar_snapshots(source_hash);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_calendar_snapshots_snapshot_key
  ON raw_calendar_snapshots(snapshot_key)
  WHERE snapshot_key IS NOT NULL AND snapshot_key != '';

CREATE INDEX IF NOT EXISTS idx_raw_calendar_snapshots_ingested_at
  ON raw_calendar_snapshots(ingested_at);

CREATE TABLE IF NOT EXISTS sync_state (
  source_name TEXT PRIMARY KEY,
  cursor_value TEXT,
  last_attempt_at TEXT,
  last_success_at TEXT,
  last_error TEXT,
  rows_imported INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  migration_id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_metadata (
  metadata_key TEXT PRIMARY KEY,
  metadata_value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_preferences (
  calendar_preference_id TEXT PRIMARY KEY,
  calendar_name TEXT NOT NULL UNIQUE,
  disposition TEXT NOT NULL DEFAULT 'review_normally',
  hidden_from_review INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'manual',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_preferences_disposition
  ON calendar_preferences(disposition, hidden_from_review, active);

CREATE TABLE IF NOT EXISTS calendar_event_candidates (
  id TEXT PRIMARY KEY,
  import_run_id TEXT NOT NULL REFERENCES import_runs(id),
  candidate_key TEXT NOT NULL,
  latest_raw_snapshot_id TEXT NOT NULL REFERENCES raw_calendar_snapshots(id),
  raw_snapshot_count INTEGER NOT NULL,
  title TEXT,
  start_at TEXT,
  end_at TEXT,
  calendar_duration_minutes INTEGER,
  calendar_name TEXT,
  capture_windows TEXT,
  classification TEXT NOT NULL,
  confidence REAL NOT NULL,
  explanation TEXT NOT NULL,
  fields_requiring_review TEXT NOT NULL,
  proposed_client_name TEXT,
  proposed_start_at TEXT,
  proposed_duration_minutes INTEGER,
  proposed_end_at TEXT,
  time_shorthand TEXT,
  duration_source TEXT,
  parser_payload TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'pending',
  confidence_label TEXT,
  unresolved_fields TEXT,
  review_reasons TEXT,
  candidate_person_names TEXT,
  possible_referenced_person TEXT,
  candidate_account_code TEXT,
  candidate_account_name TEXT,
  service_mode TEXT,
  rate_group TEXT,
  time_category TEXT,
  is_evening INTEGER NOT NULL DEFAULT 0,
  is_weekend INTEGER NOT NULL DEFAULT 0,
  appointment_status TEXT NOT NULL DEFAULT 'unresolved',
  billing_treatment TEXT NOT NULL DEFAULT 'unresolved',
  title_time_text TEXT,
  title_time_normalized TEXT,
  title_time_matches_calendar INTEGER,
  calendar_disposition TEXT NOT NULL DEFAULT 'review_normally',
  calendar_is_preferred_work INTEGER NOT NULL DEFAULT 0,
  hidden_from_review INTEGER NOT NULL DEFAULT 0,
  reconciliation_status TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(import_run_id, candidate_key)
);

CREATE INDEX IF NOT EXISTS idx_calendar_event_candidates_candidate_key
  ON calendar_event_candidates(candidate_key);

CREATE INDEX IF NOT EXISTS idx_calendar_event_candidates_calendar_filter
  ON calendar_event_candidates(calendar_disposition, hidden_from_review, calendar_name);

CREATE TABLE IF NOT EXISTS people (
  person_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  preferred_name TEXT,
  person_code TEXT UNIQUE,
  billing_email TEXT,
  billing_phone TEXT,
  administrative_notes TEXT,
  active_status TEXT NOT NULL DEFAULT 'active',
  merged_into_person_id TEXT REFERENCES people(person_id),
  merge_note TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_people_display_name
  ON people(display_name);

CREATE TABLE IF NOT EXISTS client_accounts (
  account_id TEXT PRIMARY KEY,
  account_code TEXT UNIQUE,
  account_name TEXT NOT NULL,
  account_type TEXT NOT NULL DEFAULT 'individual',
  default_billing_party_id TEXT,
  administrative_notes TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_client_accounts_account_code
  ON client_accounts(account_code);

CREATE TABLE IF NOT EXISTS account_members (
  account_member_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES client_accounts(account_id),
  person_id TEXT NOT NULL REFERENCES people(person_id),
  relationship_role TEXT NOT NULL DEFAULT 'primary',
  is_primary INTEGER NOT NULL DEFAULT 0,
  effective_from TEXT,
  effective_through TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(account_id, person_id, relationship_role)
);

CREATE TABLE IF NOT EXISTS billing_parties (
  billing_party_id TEXT PRIMARY KEY,
  billing_party_type TEXT NOT NULL,
  person_id TEXT REFERENCES people(person_id),
  organization_name TEXT,
  billing_name TEXT NOT NULL,
  billing_email TEXT,
  billing_address_line_1 TEXT,
  billing_address_line_2 TEXT,
  billing_city TEXT,
  billing_state TEXT,
  billing_postal_code TEXT,
  billing_phone TEXT,
  administrative_notes TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_aliases (
  alias_id TEXT PRIMARY KEY,
  raw_alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL UNIQUE,
  account_id TEXT REFERENCES client_accounts(account_id),
  person_id TEXT REFERENCES people(person_id),
  classification TEXT,
  service_mode TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  approved_by_user INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_aliases_normalized_alias
  ON calendar_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS rate_rules (
  rate_rule_id TEXT PRIMARY KEY,
  client_account_id TEXT REFERENCES client_accounts(account_id),
  person_id TEXT REFERENCES people(person_id),
  duration_minutes INTEGER,
  service_mode TEXT,
  rate_group TEXT,
  time_category TEXT NOT NULL DEFAULT 'standard',
  amount_cents INTEGER NOT NULL,
  modifier_type TEXT,
  modifier_amount_cents INTEGER,
  effective_from TEXT NOT NULL,
  effective_through TEXT,
  priority INTEGER NOT NULL DEFAULT 100,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rate_rules_match
  ON rate_rules(active, client_account_id, person_id, duration_minutes, service_mode, rate_group, time_category);

CREATE TABLE IF NOT EXISTS rate_rule_participants (
  rate_rule_participant_id TEXT PRIMARY KEY,
  rate_rule_id TEXT NOT NULL REFERENCES rate_rules(rate_rule_id),
  person_id TEXT NOT NULL REFERENCES people(person_id),
  created_at TEXT NOT NULL,
  UNIQUE(rate_rule_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_rate_rule_participants_person
  ON rate_rule_participants(person_id, rate_rule_id);

CREATE TABLE IF NOT EXISTS rate_policy (
  policy_name TEXT PRIMARY KEY,
  policy_value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  client_code TEXT UNIQUE,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_aliases (
  id TEXT PRIMARY KEY,
  client_id TEXT REFERENCES clients(id),
  alias TEXT NOT NULL UNIQUE,
  alias_type TEXT NOT NULL DEFAULT 'calendar_title',
  classification_hint TEXT,
  review_status TEXT NOT NULL DEFAULT 'unconfirmed',
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_rates (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL REFERENCES clients(id),
  rate_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
  source_event_candidate_id TEXT,
  client_id TEXT REFERENCES clients(id),
  account_id TEXT REFERENCES client_accounts(account_id),
  billing_party_id TEXT REFERENCES billing_parties(billing_party_id),
  proposed_client_name TEXT,
  session_date TEXT,
  start_at TEXT NOT NULL,
  end_at TEXT,
  calendar_duration_minutes INTEGER,
  parsed_duration_minutes INTEGER,
  approved_duration_minutes INTEGER,
  duration_minutes INTEGER NOT NULL,
  service_mode TEXT,
  rate_group TEXT,
  time_category TEXT,
  is_evening INTEGER NOT NULL DEFAULT 0,
  is_weekend INTEGER NOT NULL DEFAULT 0,
  suggested_rate_cents INTEGER,
  approved_rate_cents INTEGER,
  rate_rule_id TEXT REFERENCES rate_rules(rate_rule_id),
  rate_source TEXT,
  approved_rate_rule_id TEXT REFERENCES rate_rules(rate_rule_id),
  approved_rate_source TEXT,
  rate_needs_review INTEGER NOT NULL DEFAULT 1,
  rate_override_reason TEXT,
  billable_status TEXT NOT NULL DEFAULT 'proposed',
  payment_status TEXT NOT NULL DEFAULT 'unresolved',
  appointment_status TEXT NOT NULL DEFAULT 'unresolved',
  billing_treatment TEXT NOT NULL DEFAULT 'billable',
  title_time_text TEXT,
  title_time_normalized TEXT,
  title_time_matches_calendar INTEGER,
  calendar_name TEXT,
  calendar_disposition TEXT NOT NULL DEFAULT 'review_normally',
  calendar_is_preferred_work INTEGER NOT NULL DEFAULT 0,
  hidden_from_review INTEGER NOT NULL DEFAULT 0,
  rate_cents_snapshot INTEGER,
  source_raw_snapshot_id TEXT NOT NULL REFERENCES raw_calendar_snapshots(id),
  raw_calendar_title TEXT,
  review_status TEXT NOT NULL DEFAULT 'needs_review',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_review_status
  ON sessions(review_status);

CREATE TABLE IF NOT EXISTS session_participants (
  session_participant_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  person_id TEXT REFERENCES people(person_id),
  participant_name TEXT,
  participant_role TEXT NOT NULL DEFAULT 'primary',
  is_primary INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_queue (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
  review_type TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 2,
  reason TEXT NOT NULL,
  fields TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  decision_payload TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_items (
  review_item_id TEXT PRIMARY KEY,
  candidate_id TEXT REFERENCES calendar_event_candidates(id),
  session_id TEXT REFERENCES sessions(id),
  review_status TEXT NOT NULL,
  unresolved_fields TEXT NOT NULL,
  review_reasons TEXT NOT NULL,
  decision_payload TEXT,
  reviewed_at TEXT,
  decision_source TEXT,
  old_value TEXT,
  new_value TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_items_review_status
  ON review_items(review_status);

CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  details TEXT,
  created_at TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    migrate_existing_db(conn)
    conn.executescript(SCHEMA)
    migrate_phase2_columns(conn)
    conn.commit()


def migrate_existing_db(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'raw_calendar_snapshots'
        """
    ).fetchone()
    if not table:
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(raw_calendar_snapshots)").fetchall()
    }
    if "snapshot_key" not in columns:
        conn.execute("ALTER TABLE raw_calendar_snapshots ADD COLUMN snapshot_key TEXT")


def migrate_phase2_columns(conn: sqlite3.Connection) -> None:
    add_columns(
        conn,
        "people",
        {
            "billing_email": "TEXT",
            "billing_phone": "TEXT",
            "administrative_notes": "TEXT",
            "active_status": "TEXT NOT NULL DEFAULT 'active'",
            "merged_into_person_id": "TEXT",
            "merge_note": "TEXT",
        },
    )
    add_columns(
        conn,
        "calendar_event_candidates",
        {
            "confidence_label": "TEXT",
            "unresolved_fields": "TEXT",
            "review_reasons": "TEXT",
            "candidate_person_names": "TEXT",
            "possible_referenced_person": "TEXT",
            "candidate_account_code": "TEXT",
            "candidate_account_name": "TEXT",
            "service_mode": "TEXT",
            "rate_group": "TEXT",
            "time_category": "TEXT",
            "is_evening": "INTEGER NOT NULL DEFAULT 0",
            "is_weekend": "INTEGER NOT NULL DEFAULT 0",
            "appointment_status": "TEXT NOT NULL DEFAULT 'unresolved'",
            "billing_treatment": "TEXT NOT NULL DEFAULT 'unresolved'",
            "title_time_text": "TEXT",
            "title_time_normalized": "TEXT",
            "title_time_matches_calendar": "INTEGER",
            "calendar_disposition": "TEXT NOT NULL DEFAULT 'review_normally'",
            "calendar_is_preferred_work": "INTEGER NOT NULL DEFAULT 0",
            "hidden_from_review": "INTEGER NOT NULL DEFAULT 0",
            "reconciliation_status": "TEXT",
        },
    )
    add_columns(
        conn,
        "client_accounts",
        {
            "administrative_notes": "TEXT",
        },
    )
    add_columns(
        conn,
        "billing_parties",
        {
            "administrative_notes": "TEXT",
        },
    )
    add_columns(
        conn,
        "sessions",
        {
            "source_event_candidate_id": "TEXT",
            "account_id": "TEXT",
            "billing_party_id": "TEXT",
            "session_date": "TEXT",
            "calendar_duration_minutes": "INTEGER",
            "parsed_duration_minutes": "INTEGER",
            "approved_duration_minutes": "INTEGER",
            "service_mode": "TEXT",
            "rate_group": "TEXT",
            "time_category": "TEXT",
            "is_evening": "INTEGER NOT NULL DEFAULT 0",
            "is_weekend": "INTEGER NOT NULL DEFAULT 0",
            "suggested_rate_cents": "INTEGER",
            "approved_rate_cents": "INTEGER",
            "rate_rule_id": "TEXT",
            "rate_source": "TEXT",
            "approved_rate_rule_id": "TEXT",
            "approved_rate_source": "TEXT",
            "rate_needs_review": "INTEGER NOT NULL DEFAULT 1",
            "rate_override_reason": "TEXT",
            "payment_status": "TEXT NOT NULL DEFAULT 'unresolved'",
            "appointment_status": "TEXT NOT NULL DEFAULT 'unresolved'",
            "billing_treatment": "TEXT NOT NULL DEFAULT 'billable'",
            "title_time_text": "TEXT",
            "title_time_normalized": "TEXT",
            "title_time_matches_calendar": "INTEGER",
            "calendar_name": "TEXT",
            "calendar_disposition": "TEXT NOT NULL DEFAULT 'review_normally'",
            "calendar_is_preferred_work": "INTEGER NOT NULL DEFAULT 0",
            "hidden_from_review": "INTEGER NOT NULL DEFAULT 0",
            "raw_calendar_title": "TEXT",
        },
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_payment_status ON sessions(payment_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_event_candidates_candidate_key ON calendar_event_candidates(candidate_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_calendar_event_candidates_calendar_filter ON calendar_event_candidates(calendar_disposition, hidden_from_review, calendar_name)"
    )


def add_columns(
    conn: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    table = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if not table:
        return
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, column_type in columns.items():
        if column_name not in existing:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            )
