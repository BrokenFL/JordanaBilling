from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
import time
from datetime import datetime
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
  preferred_delivery_method TEXT NOT NULL DEFAULT 'unresolved',
  administrative_notes TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS business_profile (
  business_profile_id TEXT PRIMARY KEY,
  business_name TEXT NOT NULL,
  provider_display_name TEXT,
  credentials_display TEXT,
  address_line_1 TEXT,
  address_line_2 TEXT,
  city TEXT,
  state TEXT,
  postal_code TEXT,
  phone TEXT,
  email TEXT,
  payee_name TEXT,
  payment_address_line_1 TEXT,
  payment_address_line_2 TEXT,
  payment_city TEXT,
  payment_state TEXT,
  payment_postal_code TEXT,
  logo_path TEXT,
  logo_contains_business_details INTEGER NOT NULL DEFAULT 0,
  show_email_below_logo INTEGER NOT NULL DEFAULT 0,
  invoice_total_label TEXT NOT NULL DEFAULT 'TOTAL DUE',
  invoice_number_format TEXT NOT NULL DEFAULT 'YYYY-NNNN',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_business_profile_one_active
  ON business_profile(active) WHERE active = 1;

CREATE TABLE IF NOT EXISTS service_catalog (
  service_catalog_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  description TEXT,
  catalog_type TEXT NOT NULL DEFAULT 'appointment_method',
  legacy_appointment_method INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  usage_count INTEGER NOT NULL DEFAULT 0,
  first_used_at TEXT,
  last_used_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_service_catalog_active_name
  ON service_catalog(active, display_name);

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
  billing_session_type TEXT,
  appointment_status TEXT NOT NULL DEFAULT 'scheduled',
  custom_service_description TEXT,
  custom_service_code TEXT,
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

CREATE INDEX IF NOT EXISTS idx_rate_rules_status_match
  ON rate_rules(active, client_account_id, person_id, duration_minutes, appointment_status, time_category);

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
  service_catalog_id TEXT REFERENCES service_catalog(service_catalog_id),
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
  payment_status TEXT NOT NULL DEFAULT 'unpaid',
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

CREATE TABLE IF NOT EXISTS invoice_sequences (
  sequence_year INTEGER PRIMARY KEY,
  last_value INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id TEXT PRIMARY KEY,
  invoice_number TEXT UNIQUE,
  status TEXT NOT NULL DEFAULT 'draft',
  bill_to_party_id TEXT NOT NULL REFERENCES billing_parties(billing_party_id),
  billing_period_start TEXT NOT NULL,
  billing_period_end TEXT NOT NULL,
  invoice_date TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  subtotal_cents INTEGER NOT NULL DEFAULT 0,
  adjustment_cents INTEGER NOT NULL DEFAULT 0,
  total_cents INTEGER NOT NULL DEFAULT 0,
  delivery_method TEXT NOT NULL DEFAULT 'unresolved',
  bill_to_name_snapshot TEXT,
  bill_to_email_snapshot TEXT,
  bill_to_phone_snapshot TEXT,
  bill_to_address_snapshot TEXT,
  business_name_snapshot TEXT,
  provider_name_snapshot TEXT,
  credentials_snapshot TEXT,
  business_address_snapshot TEXT,
  business_phone_snapshot TEXT,
  business_email_snapshot TEXT,
  payee_name_snapshot TEXT,
  payment_address_snapshot TEXT,
  logo_reference_snapshot TEXT,
  logo_contains_business_details_snapshot INTEGER NOT NULL DEFAULT 0,
  show_email_below_logo_snapshot INTEGER NOT NULL DEFAULT 0,
  total_label_snapshot TEXT,
  number_format_snapshot TEXT,
  notes TEXT,
  void_reason TEXT,
  pdf_path TEXT,
  pdf_sha256 TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finalized_at TEXT,
  voided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_invoices_status_date
  ON invoices(status, invoice_date);

CREATE INDEX IF NOT EXISTS idx_invoices_bill_to_period
  ON invoices(bill_to_party_id, billing_period_start, billing_period_end);

CREATE TABLE IF NOT EXISTS invoice_line_items (
  invoice_line_item_id TEXT PRIMARY KEY,
  invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
  source_session_id TEXT REFERENCES sessions(id),
  sort_order INTEGER NOT NULL DEFAULT 0,
  service_date TEXT NOT NULL,
  participants_snapshot TEXT NOT NULL,
  service_catalog_id TEXT REFERENCES service_catalog(service_catalog_id),
  service_name_snapshot TEXT NOT NULL,
  billing_session_type_snapshot TEXT,
  time_category_snapshot TEXT,
  appointment_status_snapshot TEXT,
  duration_minutes INTEGER,
  description_snapshot TEXT NOT NULL,
  custom_service_description_snapshot TEXT,
  custom_service_code_snapshot TEXT,
  quantity INTEGER NOT NULL DEFAULT 1,
  unit_amount_cents INTEGER NOT NULL,
  line_amount_cents INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invoice_line_items_invoice_order
  ON invoice_line_items(invoice_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_invoice_line_items_source_session
  ON invoice_line_items(source_session_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_line_items_invoice_session
  ON invoice_line_items(invoice_id, source_session_id)
  WHERE source_session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS custom_service_mappings (
  mapping_id TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES people(person_id),
  duration_choice TEXT NOT NULL,
  custom_description TEXT NOT NULL,
  custom_code TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(person_id, duration_choice, active) 
);

CREATE INDEX IF NOT EXISTS idx_custom_service_mappings_person
  ON custom_service_mappings(person_id, duration_choice, active);
"""


CURRENT_SCHEMA_VERSION = "001_base"

DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0


class MigrationError(Exception):
    """Raised when a database migration fails."""

    def __init__(self, message: str, backup_path: str | None = None) -> None:
        super().__init__(message)
        self.backup_path = backup_path


class LockError(Exception):
    """Raised when a database lock cannot be acquired within the timeout."""


class DatabaseBusyError(Exception):
    """Raised when the database is locked by another operation."""


class DatabaseLock:
    """File-based lock for protecting bulk database operations.

    Uses fcntl.flock for cross-process and cross-thread locking.
    Stale locks are automatically recovered because the OS releases
    flock when the holding process exits.
    """

    def __init__(
        self, db_path: str | Path, timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    ) -> None:
        self.lock_path = Path(str(db_path) + ".lock")
        self.timeout_seconds = timeout_seconds
        self._fd: int | None = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(self._fd, 0)
                os.write(self._fd, f"{os.getpid()}\n".encode())
                return
            except (BlockingIOError, OSError):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    os.close(self._fd)
                    self._fd = None
                    raise LockError(
                        f"Could not acquire database lock within {self.timeout_seconds}s. "
                        f"Another sync or migration may be in progress. "
                        f"Lock file: {self.lock_path}"
                    )
                time.sleep(min(0.5, remaining))

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> "DatabaseLock":
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


def _get_applied_migrations(conn: sqlite3.Connection) -> set[str]:
    try:
        return {
            row["migration_id"]
            for row in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
        }
    except sqlite3.OperationalError:
        return set()


def _create_backup(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.parent / f"{db_path.stem}.backup-migrate-{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _verify_backup(backup_path: Path) -> None:
    if not backup_path.exists():
        raise MigrationError(f"Backup file was not created: {backup_path}")
    test_conn = sqlite3.connect(str(backup_path))
    try:
        result = test_conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            raise MigrationError(f"Backup integrity check failed: {backup_path}")
    finally:
        test_conn.close()


def _apply_migration_001(conn: sqlite3.Connection) -> None:
    migrate_existing_db(conn)
    conn.executescript(SCHEMA)
    migrate_phase2_columns(conn)
    seed_service_catalog(conn)


MIGRATIONS: list[tuple[str, object]] = [
    (CURRENT_SCHEMA_VERSION, _apply_migration_001),
]


def migrate_database(db_path: str | Path) -> dict:
    """Run pending database migrations with backup and rollback.

    - If the schema is already current, does nothing.
    - For existing databases needing migration, creates a timestamped backup first.
    - Runs migrations transactionally.
    - On failure, rolls back and restores the original database.
    - Acquires a file-based lock to prevent concurrent sync or migration.
    """
    from .util import now_iso

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not db_path.exists()

    lock = DatabaseLock(db_path)
    try:
        lock.acquire()
    except LockError as error:
        raise MigrationError(str(error)) from error

    try:
        backup_path: Path | None = None
        if not is_new:
            backup_path = _create_backup(db_path)
            _verify_backup(backup_path)

        conn = connect(db_path)

        applied = _get_applied_migrations(conn)
        pending = [(mid, fn) for mid, fn in MIGRATIONS if mid not in applied]

        if not pending:
            conn.close()
            if backup_path:
                backup_path.unlink()
            return {"migrated": False, "backup_path": None, "is_new": is_new}

        try:
            conn.execute("BEGIN")
            for mid, migration_fn in pending:
                migration_fn(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (mid, now_iso()),
                )
            conn.commit()
            conn.close()
            return {
                "migrated": True,
                "backup_path": str(backup_path) if backup_path else None,
                "is_new": is_new,
            }
        except Exception as error:
            conn.rollback()
            conn.close()
            if backup_path:
                shutil.copy2(backup_path, db_path)
            raise MigrationError(
                f"Migration failed: {error}",
                backup_path=str(backup_path) if backup_path else None,
            ) from error
    finally:
        lock.release()


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    from .util import now_iso

    applied = _get_applied_migrations(conn)
    if CURRENT_SCHEMA_VERSION in applied:
        return
    migrate_existing_db(conn)
    conn.executescript(SCHEMA)
    migrate_phase2_columns(conn)
    seed_service_catalog(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
        (CURRENT_SCHEMA_VERSION, now_iso()),
    )
    conn.commit()


def migrate_existing_db(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'raw_calendar_snapshots'
        """
    ).fetchone()
    if table:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(raw_calendar_snapshots)").fetchall()
        }
        if "snapshot_key" not in columns:
            conn.execute("ALTER TABLE raw_calendar_snapshots ADD COLUMN snapshot_key TEXT")
    rate_rules = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'rate_rules'
        """
    ).fetchone()
    if rate_rules:
        rate_rule_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(rate_rules)").fetchall()
        }
        if "appointment_status" not in rate_rule_columns:
            conn.execute(
                "ALTER TABLE rate_rules ADD COLUMN appointment_status TEXT NOT NULL DEFAULT 'scheduled'"
            )


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
            "billing_session_type": "TEXT",
            "appointment_method": "TEXT",
            "duration_choice": "TEXT",
            "house_call_suggested": "INTEGER NOT NULL DEFAULT 0",
            "billing_type_source": "TEXT",
            "location_text": "TEXT",
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
            "preferred_delivery_method": "TEXT NOT NULL DEFAULT 'unresolved'",
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
            "service_catalog_id": "TEXT",
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
            "payment_status": "TEXT NOT NULL DEFAULT 'unpaid'",
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
            "billing_session_type": "TEXT",
            "appointment_method": "TEXT",
            "duration_choice": "TEXT",
            "custom_duration_minutes": "INTEGER",
            "house_call_suggested": "INTEGER NOT NULL DEFAULT 0",
            "billing_type_source": "TEXT",
            "custom_service_description": "TEXT",
            "custom_service_code": "TEXT",
            "location_text": "TEXT",
        },
    )
    add_columns(
        conn,
        "service_catalog",
        {
            "catalog_type": "TEXT NOT NULL DEFAULT 'appointment_method'",
            "legacy_appointment_method": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    add_columns(
        conn,
        "invoices",
        {
            "revision": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    add_columns(
        conn,
        "invoice_line_items",
        {
            "billing_session_type_snapshot": "TEXT",
            "custom_service_description_snapshot": "TEXT",
            "custom_service_code_snapshot": "TEXT",
        },
    )
    add_columns(
        conn,
        "rate_rules",
        {
            "billing_session_type": "TEXT",
            "appointment_status": "TEXT NOT NULL DEFAULT 'scheduled'",
            "custom_service_description": "TEXT",
            "custom_service_code": "TEXT",
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rate_rules_custom_match ON rate_rules(active, billing_session_type, custom_service_code)"
    )


def seed_service_catalog(conn: sqlite3.Connection) -> None:
    from .util import new_id, now_iso

    now = now_iso()
    for canonical, display, catalog_type, legacy in (
        ("psychotherapy", "Psychotherapy Session", "billing_session_type", 0),
        ("psychotherapy_house_call", "Psychotherapy Session / House Call", "billing_session_type", 0),
        ("psychotherapy_weekend", "Psychotherapy Session / Weekend", "billing_session_type", 0),
        ("psychotherapy_evening", "Psychotherapy Session / Evening", "billing_session_type", 0),
        ("custom", "Custom", "billing_session_type", 0),
        ("office", "Office", "appointment_method", 1),
        ("phone", "Phone", "appointment_method", 1),
        ("facetime", "FaceTime", "appointment_method", 1),
        ("house_call", "House Call", "appointment_method", 0),
        ("correspondence", "Correspondence", "appointment_method", 0),
        ("preparation", "Preparation", "appointment_method", 0),
        ("mediation", "Mediation", "appointment_method", 0),
        ("other", "Other", "appointment_method", 0),
    ):
        conn.execute(
            """
            INSERT INTO service_catalog (
              service_catalog_id, canonical_name, normalized_name, display_name,
              catalog_type, legacy_appointment_method, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(normalized_name) DO UPDATE SET
              catalog_type = excluded.catalog_type,
              legacy_appointment_method = excluded.legacy_appointment_method,
              updated_at = excluded.updated_at
            """,
            (new_id(), canonical, canonical, display, catalog_type, legacy, now, now),
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
