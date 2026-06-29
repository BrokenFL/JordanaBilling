from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Operational database identity
# ---------------------------------------------------------------------------

# The CLI default database path.  When JORDANA_DATABASE_PATH is not set in
# the environment, this relative path (resolved against the current working
# directory) is the authoritative operational database location.
_DEFAULT_DB_PATH = "data/jordana_invoice.sqlite3"


class OperationalDatabaseError(Exception):
    """Raised when a destructive operation targets the operational database
    without explicit authorization."""


@dataclass(frozen=True)
class OperationalImportAuthorization:
    """Deliberate authorization object for importing into the operational DB.

    Created by :func:`authorize_operational_import` after validating that
    the caller has confirmed the exact canonical operational path and that
    a verified backup has been created.

    Pass this to :func:`import_csv` (via ``operational_authorization=``) or to
    :func:`assert_csv_import_safe` to prove the import is intentional.
    """
    confirmed_path: Path
    backup_path: Path | None = None


def get_configured_operational_db_path() -> Path:
    """Return the one authoritative configured operational database path.

    The path is determined from (in priority order):

    1.  ``JORDANA_DATABASE_PATH`` environment variable (typically set by
        ``.env`` / ``bootstrap.sh``).
    2.  The CLI default ``data/jordana_invoice.sqlite3`` resolved against
        the current working directory.

    The returned path is **not** resolved — callers should ``.resolve()``
    it when comparing against a candidate path so that symlinks and
    relative paths are normalised consistently.
    """
    env_path = os.environ.get("JORDANA_DATABASE_PATH")
    if env_path:
        return Path(os.path.expanduser(env_path))
    return Path(_DEFAULT_DB_PATH)


def is_operational_db_path(path: str | Path) -> bool:
    """Return True when *path* resolves to the configured operational database.

    Both the candidate path and the configured operational path are
    canonicalised with ``Path.resolve()`` (which follows symlinks and
    normalises relative paths) before comparison.  This means:

    - Relative paths are resolved against the current working directory.
    - Symlinks are followed to their real target.
    - ``./`` prefixes and ``../`` traversal are normalised.

    The function returns ``True`` only when the canonical paths are equal.
    """
    try:
        candidate = Path(path).expanduser().resolve()
        configured = get_configured_operational_db_path().expanduser().resolve()
    except (TypeError, ValueError, OSError):
        return False
    return candidate == configured


def _get_db_path_from_conn(conn: sqlite3.Connection) -> Path | None:
    """Extract the main database file path from a live connection."""
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        for row in rows:
            if row[1] == "main" and row[2]:
                return Path(row[2])
    except (sqlite3.Error, IndexError, TypeError):
        pass
    return None


def authorize_operational_import(
    db_path: str | Path,
    confirmed_path: str | Path | None = None,
) -> OperationalImportAuthorization:
    """Validate authorization and create a verified backup before any mutation.

    This function should be called **before** migration or import when the
    target database is the configured operational database.

    Parameters:
        db_path: The database path being targeted.
        confirmed_path: The exact canonical path the caller confirmed.
            Must resolve to the same canonical path as the configured
            operational database.  Required for operational databases.

    Returns:
        An :class:`OperationalImportAuthorization` with the confirmed path
        and backup path.

    Raises:
        OperationalDatabaseError: If the database is operational but
            *confirmed_path* is missing or does not match.
    """
    if not is_operational_db_path(db_path):
        raise OperationalDatabaseError(
            f"authorize_operational_import called for a non-operational "
            f"database: {db_path}"
        )

    configured = get_configured_operational_db_path().expanduser().resolve()

    if confirmed_path is None:
        raise OperationalDatabaseError(
            f"Refused: importing into the operational database ({configured}) "
            f"requires explicit path confirmation. "
            f"Provide --confirm-operational-db-path with the exact canonical path."
        )

    try:
        confirmed_resolved = Path(confirmed_path).expanduser().resolve()
    except (TypeError, ValueError, OSError) as error:
        raise OperationalDatabaseError(
            f"Refused: could not resolve confirmation path: {error}"
        ) from error

    if confirmed_resolved != configured:
        raise OperationalDatabaseError(
            f"Refused: confirmation path ({confirmed_resolved}) does not match "
            f"the configured operational database ({configured})."
        )

    # Authorization confirmed — create and verify backup.
    db_path_obj = Path(db_path)
    backup_path: Path | None = None
    if db_path_obj.exists():
        backup_path = _create_backup(db_path_obj)
        _verify_backup(backup_path)

    return OperationalImportAuthorization(
        confirmed_path=confirmed_resolved,
        backup_path=backup_path,
    )


def assert_csv_import_safe(
    conn: sqlite3.Connection,
    authorization: OperationalImportAuthorization | None = None,
) -> Path | None:
    """Safety guard for CSV imports into the operational database.

    If the connection's database resolves to the configured operational path:

    - **No authorization** → raise ``OperationalDatabaseError``.
    - **Boolean or other non-authorization type** → raise
      ``OperationalDatabaseError`` (a plain ``True`` is no longer accepted).
    - **Valid authorization** with ``confirmed_path`` matching the
      connection's database → allow (backup already created by
      :func:`authorize_operational_import`, no duplicate).
    - **Authorization with mismatched confirmed_path** → raise
      ``OperationalDatabaseError``.

    For non-operational databases, always returns ``None`` (no guard).

    Returns the backup path (or ``None`` if no backup was needed).
    """
    db_path = _get_db_path_from_conn(conn)
    if db_path is None:
        return None
    if not is_operational_db_path(db_path):
        return None

    configured = get_configured_operational_db_path().resolve()

    if not isinstance(authorization, OperationalImportAuthorization):
        raise OperationalDatabaseError(
            f"Refused: the database at {db_path} is the configured "
            f"operational database ({configured}). "
            f"CSV imports can overwrite manual review decisions and "
            f"approved sessions. "
            f"Use scripts/run_acceptance_test.sh for acceptance testing. "
            f"If you genuinely need to import into the live database, "
            f"obtain an OperationalImportAuthorization from "
            f"authorize_operational_import()."
        )

    # Validate that the authorization's confirmed path matches the
    # actual connection database path (not just the configured path).
    try:
        conn_resolved = db_path.expanduser().resolve()
    except (TypeError, ValueError, OSError):
        conn_resolved = db_path

    if authorization.confirmed_path != conn_resolved:
        raise OperationalDatabaseError(
            f"Refused: authorization confirmed_path "
            f"({authorization.confirmed_path}) does not match the "
            f"actual database path ({conn_resolved})."
        )

    # Valid authorization — backup already created. No duplicate.
    return authorization.backup_path


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
  last_mode TEXT,
  last_rows_fetched INTEGER NOT NULL DEFAULT 0,
  last_rows_imported INTEGER NOT NULL DEFAULT 0,
  last_duplicate_rows INTEGER NOT NULL DEFAULT 0,
  last_review_items_changed INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS candidate_identity_aliases (
  alias_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
  alias_type TEXT NOT NULL CHECK (alias_type IN ('calendar_event_id', 'event_fingerprint', 'structural')),
  alias_value TEXT NOT NULL,
  source_raw_snapshot_id TEXT REFERENCES raw_calendar_snapshots(id),
  resolution_reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(alias_type, alias_value, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_identity_alias_lookup
  ON candidate_identity_aliases(alias_type, alias_value);

CREATE INDEX IF NOT EXISTS idx_candidate_identity_alias_candidate
  ON candidate_identity_aliases(candidate_id);

CREATE TABLE IF NOT EXISTS candidate_duplicate_reconciliations (
  reconciliation_id TEXT PRIMARY KEY,
  canonical_candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
  duplicate_candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
  canonical_session_id TEXT REFERENCES sessions(id),
  duplicate_session_id TEXT REFERENCES sessions(id),
  status TEXT NOT NULL CHECK (status IN ('planned', 'applied', 'reversed', 'manual_review')),
  reason TEXT NOT NULL,
  original_state_json TEXT,
  applied_state_json TEXT,
  applied_at TEXT,
  reversed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(duplicate_candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_duplicate_reconciliations_canonical
  ON candidate_duplicate_reconciliations(canonical_candidate_id);

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
  default_filing_owner_person_id TEXT REFERENCES people(person_id),
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

CREATE TABLE IF NOT EXISTS billing_relationship_keys (
  account_id TEXT PRIMARY KEY REFERENCES client_accounts(account_id),
  payer_identity_key TEXT NOT NULL,
  payer_kind TEXT NOT NULL,
  payer_person_id TEXT REFERENCES people(person_id),
  payer_billing_party_id TEXT REFERENCES billing_parties(billing_party_id),
  covered_client_key TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_relationship_keys_active_unique
  ON billing_relationship_keys(payer_identity_key, covered_client_key)
  WHERE active = 1;

CREATE INDEX IF NOT EXISTS idx_billing_relationship_keys_payer
  ON billing_relationship_keys(payer_identity_key, active);

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
  zelle_recipient TEXT,
  logo_path TEXT,
  logo_contains_business_details INTEGER NOT NULL DEFAULT 0,
  show_email_below_logo INTEGER NOT NULL DEFAULT 0,
  invoice_total_label TEXT NOT NULL DEFAULT 'TOTAL DUE',
  invoice_number_format TEXT NOT NULL DEFAULT 'YYYY-NNNN',
  insurance_ein TEXT,
  insurance_npi TEXT,
  insurance_sw TEXT,
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

CREATE TABLE IF NOT EXISTS receipt_sequences (
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
  zelle_recipient_snapshot TEXT,
  filing_owner_person_id TEXT REFERENCES people(person_id),
  filing_owner_person_code_snapshot TEXT,
  filing_owner_display_name_snapshot TEXT,
  logo_reference_snapshot TEXT,
  logo_contains_business_details_snapshot INTEGER NOT NULL DEFAULT 0,
  show_email_below_logo_snapshot INTEGER NOT NULL DEFAULT 0,
  total_label_snapshot TEXT,
  number_format_snapshot TEXT,
  insurance_coding_included INTEGER NOT NULL DEFAULT 0,
  insurance_diagnosis_code_snapshot TEXT,
  insurance_ein_snapshot TEXT,
  insurance_npi_snapshot TEXT,
  insurance_sw_snapshot TEXT,
  notes TEXT,
  void_reason TEXT,
  pdf_path TEXT,
  pdf_sha256 TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  billing_month TEXT,
  supplement_sequence INTEGER NOT NULL DEFAULT 0 CHECK (supplement_sequence >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finalized_at TEXT,
  voided_at TEXT,
  account_summary_snapshot TEXT
);

CREATE INDEX IF NOT EXISTS idx_invoices_status_date
  ON invoices(status, invoice_date);

CREATE INDEX IF NOT EXISTS idx_invoices_bill_to_period
  ON invoices(bill_to_party_id, billing_period_start, billing_period_end);

CREATE TABLE IF NOT EXISTS payment_receipts (
  receipt_id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL UNIQUE REFERENCES payments(payment_id),
  receipt_number TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'finalized' CHECK (status IN ('finalized')),
  payment_received_at TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  filing_owner_person_id TEXT REFERENCES people(person_id),
  filing_owner_person_code_snapshot TEXT,
  filing_owner_display_name_snapshot TEXT,
  snapshot_json TEXT NOT NULL,
  pdf_path TEXT NOT NULL,
  pdf_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_receipts_payment
  ON payment_receipts(payment_id);

CREATE INDEX IF NOT EXISTS idx_payment_receipts_filing_owner
  ON payment_receipts(filing_owner_person_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_draft_party_month
  ON invoices(bill_to_party_id, billing_month)
  WHERE status = 'draft' AND billing_month IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS payments (
  payment_id TEXT PRIMARY KEY,
  billing_party_id TEXT NOT NULL
    REFERENCES billing_parties(billing_party_id),
  amount_cents INTEGER NOT NULL
    CHECK (amount_cents > 0),
  received_at TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT 'other',
  reference_number TEXT,
  received_from_name TEXT,
  administrative_note TEXT,
  status TEXT NOT NULL DEFAULT 'posted'
    CHECK (status IN ('posted', 'void')),
  source_type TEXT NOT NULL DEFAULT 'manual'
    CHECK (source_type IN ('manual', 'paid_at_session_backfill')),
  source_session_id TEXT
    REFERENCES sessions(id),
  voided_at TEXT,
  void_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_billing_party
  ON payments(billing_party_id, received_at);

CREATE INDEX IF NOT EXISTS idx_payments_status
  ON payments(status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_paid_at_session_source
  ON payments(source_session_id)
  WHERE source_type = 'paid_at_session_backfill'
    AND source_session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS payment_allocations (
  allocation_id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL
    REFERENCES payments(payment_id),
  session_id TEXT NOT NULL
    REFERENCES sessions(id),
  invoice_line_item_id TEXT
    REFERENCES invoice_line_items(invoice_line_item_id),
  amount_cents INTEGER NOT NULL
    CHECK (amount_cents > 0),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'reversed')),
  reversed_at TEXT,
  reversal_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_allocations_payment
  ON payment_allocations(payment_id);

CREATE INDEX IF NOT EXISTS idx_allocations_session
  ON payment_allocations(session_id);

CREATE INDEX IF NOT EXISTS idx_allocations_invoice_line
  ON payment_allocations(invoice_line_item_id)
  WHERE invoice_line_item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_allocations_session_active
  ON payment_allocations(session_id, status)
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS idempotency_keys (
  idempotency_key TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_entity
  ON idempotency_keys(entity_type, entity_id, action);
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


def _backup_sqlite_database(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    source_conn = sqlite3.connect(
        str(source_path),
        timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000.0,
    )
    destination_conn = sqlite3.connect(
        str(destination_path),
        timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000.0,
    )
    try:
        source_conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
        destination_conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()


def get_backup_dir() -> Path:
    env_dir = os.environ.get("JORDANA_BACKUP_DIR")
    if env_dir:
        return Path(os.path.expanduser(env_dir))
    return Path(os.path.expanduser("~/.jordana_invoice/backups"))


def _create_backup(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.stem}.backup-migrate-{timestamp}{db_path.suffix}"
    # Handle same-second collisions by appending a counter.
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{db_path.stem}.backup-migrate-{timestamp}-{counter}{db_path.suffix}"
        counter += 1
    _backup_sqlite_database(db_path, backup_path)
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


MIGRATION_002_MONTHLY_INVOICE_IDENTITY = "002_monthly_invoice_identity"


def _is_complete_calendar_month(start: str, end: str) -> bool:
    """Return True if start..end is exactly one complete calendar month."""
    from datetime import date as _date, timedelta
    try:
        d_start = _date.fromisoformat(start[:10])
        d_end = _date.fromisoformat(end[:10])
    except (ValueError, TypeError):
        return False
    if d_start.day != 1:
        return False
    if d_start.year != d_end.year or d_start.month != d_end.month:
        return False
    if d_start.month == 12:
        last = _date(d_start.year + 1, 1, 1) - timedelta(days=1)
    else:
        last = _date(d_start.year, d_start.month + 1, 1) - timedelta(days=1)
    return d_end == last


def _backfill_billing_month(conn: sqlite3.Connection) -> None:
    """Backfill billing_month for invoices whose period is exactly one calendar month."""
    rows = conn.execute(
        "SELECT invoice_id, billing_period_start, billing_period_end FROM invoices WHERE billing_month IS NULL"
    ).fetchall()
    for row in rows:
        start = str(row["billing_period_start"] or "")
        end = str(row["billing_period_end"] or "")
        if _is_complete_calendar_month(start, end):
            month = start[:7]
            conn.execute(
                "UPDATE invoices SET billing_month = ? WHERE invoice_id = ?",
                (month, row["invoice_id"]),
            )


def _check_duplicate_draft_months(conn: sqlite3.Connection) -> None:
    """Abort migration if two draft invoices share the same bill_to_party_id + billing_month."""
    duplicates = conn.execute(
        """
        SELECT bill_to_party_id, billing_month, COUNT(*) AS dup_count
        FROM invoices
        WHERE status = 'draft' AND billing_month IS NOT NULL
        GROUP BY bill_to_party_id, billing_month
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if duplicates:
        pairs = ", ".join(
            f"party={row['bill_to_party_id']} month={row['billing_month']} ({row['dup_count']} drafts)"
            for row in duplicates
        )
        raise MigrationError(
            f"Cannot create unique index: duplicate draft invoices found for the same "
            f"Bill To party and billing month. Resolve manually before re-running migration. "
            f"Duplicates: {pairs}"
        )


def _apply_migration_002(conn: sqlite3.Connection) -> None:
    add_columns(
        conn,
        "invoices",
        {
            "billing_month": "TEXT",
            "supplement_sequence": "INTEGER NOT NULL DEFAULT 0 CHECK (supplement_sequence >= 0)",
        },
    )
    _backfill_billing_month(conn)
    _check_duplicate_draft_months(conn)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_draft_party_month"
        " ON invoices(bill_to_party_id, billing_month)"
        " WHERE status = 'draft' AND billing_month IS NOT NULL"
    )


MIGRATION_003_PAYMENT_LEDGER_FOUNDATION = "003_payment_ledger_foundation"


def _apply_migration_003(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


MIGRATION_004_PAYMENT_PROVENANCE = "004_payment_provenance"


def _apply_migration_004(conn: sqlite3.Connection) -> None:
    add_columns(conn, "payments", {
        "source_type": "TEXT NOT NULL DEFAULT 'manual' CHECK (source_type IN ('manual', 'paid_at_session_backfill'))",
        "source_session_id": "TEXT REFERENCES sessions(id)",
    })
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_paid_at_session_source"
        " ON payments(source_session_id)"
        " WHERE source_type = 'paid_at_session_backfill'"
        " AND source_session_id IS NOT NULL"
    )


MIGRATION_005_INVOICE_LINE_CORRECTIONS_AUDIT = "005_invoice_line_corrections_audit"


def _apply_migration_005(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS invoice_line_item_corrections (
          correction_id TEXT PRIMARY KEY,
          invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
          invoice_line_item_id TEXT NOT NULL REFERENCES invoice_line_items(invoice_line_item_id),
          source_session_id TEXT REFERENCES sessions(id),
          old_description TEXT NOT NULL,
          new_description TEXT NOT NULL,
          old_amount_cents INTEGER NOT NULL,
          new_amount_cents INTEGER NOT NULL,
          correction_scope TEXT NOT NULL CHECK (correction_scope IN ('invoice_line_only', 'invoice_line_and_session')),
          reason TEXT NOT NULL,
          created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoice_line_item_corrections_invoice"
        " ON invoice_line_item_corrections(invoice_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoice_line_item_corrections_line"
        " ON invoice_line_item_corrections(invoice_line_item_id)"
    )


MIGRATION_006_INVOICE_ZELLE_AND_DELIVERY = "006_invoice_zelle_and_delivery"


def _apply_migration_006(conn: sqlite3.Connection) -> None:
    add_columns(
        conn,
        "business_profile",
        {
            "zelle_recipient": "TEXT",
        },
    )
    add_columns(
        conn,
        "invoices",
        {
            "zelle_recipient_snapshot": "TEXT",
        },
    )


MIGRATION_007_PAYMENT_CORRECTIONS = "007_payment_corrections"


def _apply_migration_007(conn: sqlite3.Connection) -> None:
    add_columns(conn, "payments", {"void_reason": "TEXT"})
    add_columns(conn, "payment_allocations", {"reversal_reason": "TEXT"})
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
          idempotency_key TEXT PRIMARY KEY,
          entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          action TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_idempotency_keys_entity
          ON idempotency_keys(entity_type, entity_id, action);
    """)


MIGRATION_008_BILLING_RELATIONSHIP_KEYS = "008_billing_relationship_keys"


def _billing_relationship_key_parts_for_account(
    conn: sqlite3.Connection,
    account_id: str,
) -> tuple[str, str, str | None, str | None, str] | None:
    row = conn.execute(
        """
        SELECT
          ca.account_id,
          ca.active AS account_active,
          bp.billing_party_id,
          bp.billing_party_type,
          bp.person_id
        FROM client_accounts ca
        LEFT JOIN billing_parties bp ON bp.billing_party_id = ca.default_billing_party_id
        WHERE ca.account_id = ?
        """,
        (account_id,),
    ).fetchone()
    if not row or not row["account_active"] or not row["billing_party_id"]:
        return None
    covered_ids = sorted(
        {
            member["person_id"]
            for member in conn.execute(
                "SELECT person_id FROM account_members WHERE account_id = ?",
                (account_id,),
            ).fetchall()
            if member["person_id"]
        }
    )
    if not covered_ids:
        return None
    if row["billing_party_type"] == "organization":
        payer_kind = "organization"
        payer_identity_key = f"organization:{row['billing_party_id']}"
        payer_person_id = None
        payer_billing_party_id = row["billing_party_id"]
    elif row["person_id"]:
        payer_kind = "person"
        payer_identity_key = f"person:{row['person_id']}"
        payer_person_id = row["person_id"]
        payer_billing_party_id = row["billing_party_id"]
    else:
        return None
    return (
        payer_identity_key,
        payer_kind,
        payer_person_id,
        payer_billing_party_id,
        ",".join(covered_ids),
    )


def _apply_migration_008(conn: sqlite3.Connection) -> None:
    from .util import now_iso

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS billing_relationship_keys (
          account_id TEXT PRIMARY KEY REFERENCES client_accounts(account_id),
          payer_identity_key TEXT NOT NULL,
          payer_kind TEXT NOT NULL,
          payer_person_id TEXT REFERENCES people(person_id),
          payer_billing_party_id TEXT REFERENCES billing_parties(billing_party_id),
          covered_client_key TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_relationship_keys_active_unique
          ON billing_relationship_keys(payer_identity_key, covered_client_key)
          WHERE active = 1;
        CREATE INDEX IF NOT EXISTS idx_billing_relationship_keys_payer
          ON billing_relationship_keys(payer_identity_key, active);
        """
    )
    applied_at = now_iso()
    conn.execute("DELETE FROM billing_relationship_keys")
    for row in conn.execute(
        "SELECT account_id FROM client_accounts WHERE active = 1 ORDER BY account_id"
    ).fetchall():
        parts = _billing_relationship_key_parts_for_account(conn, row["account_id"])
        if not parts:
            continue
        try:
            conn.execute(
                """
                INSERT INTO billing_relationship_keys (
                  account_id,
                  payer_identity_key,
                  payer_kind,
                  payer_person_id,
                  payer_billing_party_id,
                  covered_client_key,
                  active,
                  created_at,
                  updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (row["account_id"], *parts, applied_at, applied_at),
            )
        except sqlite3.IntegrityError:
            # Legacy duplicate active relationships are left out of the helper table
            # so they can be reviewed explicitly instead of being silently rewritten.
            continue


MIGRATION_009_INVOICE_FILING_OWNER = "009_invoice_filing_owner"


def _apply_migration_009(conn: sqlite3.Connection) -> None:
    add_columns(conn, "client_accounts", {
        "default_filing_owner_person_id": "TEXT REFERENCES people(person_id)",
    })
    add_columns(conn, "invoices", {
        "filing_owner_person_id": "TEXT REFERENCES people(person_id)",
        "filing_owner_person_code_snapshot": "TEXT",
        "filing_owner_display_name_snapshot": "TEXT",
    })
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_invoices_filing_owner"
        " ON invoices(filing_owner_person_id, status)"
    )


MIGRATION_010_INVOICE_PRIOR_BALANCE_SNAPSHOTS = "010_invoice_prior_balance_snapshots"


def _apply_migration_010(conn: sqlite3.Connection) -> None:
    add_columns(conn, "invoices", {
        "account_summary_snapshot": "TEXT",
    })


MIGRATION_011_PAYMENT_RECEIPTS = "011_payment_receipts"


def _apply_migration_011(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS receipt_sequences (
          sequence_year INTEGER PRIMARY KEY,
          last_value INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS payment_receipts (
          receipt_id TEXT PRIMARY KEY,
          payment_id TEXT NOT NULL UNIQUE REFERENCES payments(payment_id),
          receipt_number TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'finalized' CHECK (status IN ('finalized')),
          payment_received_at TEXT NOT NULL,
          amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
          filing_owner_person_id TEXT REFERENCES people(person_id),
          filing_owner_person_code_snapshot TEXT,
          filing_owner_display_name_snapshot TEXT,
          snapshot_json TEXT NOT NULL,
          pdf_path TEXT NOT NULL,
          pdf_sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payment_receipts_payment"
        " ON payment_receipts(payment_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payment_receipts_filing_owner"
        " ON payment_receipts(filing_owner_person_id, created_at)"
    )


MIGRATION_012_INSURANCE_CODING = "012_insurance_coding"


def _apply_migration_012(conn: sqlite3.Connection) -> None:
    add_columns(
        conn,
        "business_profile",
        {
            "insurance_ein": "TEXT",
            "insurance_npi": "TEXT",
            "insurance_sw": "TEXT",
        },
    )
    add_columns(
        conn,
        "invoices",
        {
            "insurance_coding_included": "INTEGER NOT NULL DEFAULT 0",
            "insurance_diagnosis_code_snapshot": "TEXT",
            "insurance_ein_snapshot": "TEXT",
            "insurance_npi_snapshot": "TEXT",
            "insurance_sw_snapshot": "TEXT",
        },
    )


MIGRATION_013_SYNC_STATE_HARDENING = "013_sync_state_hardening"


def _apply_migration_013(conn: sqlite3.Connection) -> None:
    add_columns(
        conn,
        "sync_state",
        {
            "last_mode": "TEXT",
            "last_rows_fetched": "INTEGER NOT NULL DEFAULT 0",
            "last_rows_imported": "INTEGER NOT NULL DEFAULT 0",
            "last_duplicate_rows": "INTEGER NOT NULL DEFAULT 0",
            "last_review_items_changed": "INTEGER NOT NULL DEFAULT 0",
        },
    )


MIGRATION_014_CANDIDATE_IDENTITY_ALIASES = "014_candidate_identity_aliases"


def _apply_migration_014(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidate_identity_aliases (
          alias_id TEXT PRIMARY KEY,
          candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
          alias_type TEXT NOT NULL CHECK (alias_type IN ('calendar_event_id', 'event_fingerprint', 'structural')),
          alias_value TEXT NOT NULL,
          source_raw_snapshot_id TEXT REFERENCES raw_calendar_snapshots(id),
          resolution_reason TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(alias_type, alias_value, candidate_id)
        );

        CREATE INDEX IF NOT EXISTS idx_candidate_identity_alias_lookup
          ON candidate_identity_aliases(alias_type, alias_value);

        CREATE INDEX IF NOT EXISTS idx_candidate_identity_alias_candidate
          ON candidate_identity_aliases(candidate_id);

        CREATE TABLE IF NOT EXISTS candidate_duplicate_reconciliations (
          reconciliation_id TEXT PRIMARY KEY,
          canonical_candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
          duplicate_candidate_id TEXT NOT NULL REFERENCES calendar_event_candidates(id),
          canonical_session_id TEXT REFERENCES sessions(id),
          duplicate_session_id TEXT REFERENCES sessions(id),
          status TEXT NOT NULL CHECK (status IN ('planned', 'applied', 'reversed', 'manual_review')),
          reason TEXT NOT NULL,
          original_state_json TEXT,
          applied_state_json TEXT,
          applied_at TEXT,
          reversed_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(duplicate_candidate_id)
        );

        CREATE INDEX IF NOT EXISTS idx_candidate_duplicate_reconciliations_canonical
          ON candidate_duplicate_reconciliations(canonical_candidate_id);
        """
    )


MIGRATION_015_DUPLICATE_REPAIR_REVERSAL_STATE = "015_duplicate_repair_reversal_state"


def _apply_migration_015(conn: sqlite3.Connection) -> None:
    add_columns(
        conn,
        "candidate_duplicate_reconciliations",
        {
            "original_state_json": "TEXT",
            "applied_state_json": "TEXT",
        },
    )


MIGRATIONS: list[tuple[str, object]] = [
    (CURRENT_SCHEMA_VERSION, _apply_migration_001),
    (MIGRATION_002_MONTHLY_INVOICE_IDENTITY, _apply_migration_002),
    (MIGRATION_003_PAYMENT_LEDGER_FOUNDATION, _apply_migration_003),
    (MIGRATION_004_PAYMENT_PROVENANCE, _apply_migration_004),
    (MIGRATION_005_INVOICE_LINE_CORRECTIONS_AUDIT, _apply_migration_005),
    (MIGRATION_006_INVOICE_ZELLE_AND_DELIVERY, _apply_migration_006),
    (MIGRATION_007_PAYMENT_CORRECTIONS, _apply_migration_007),
    (MIGRATION_008_BILLING_RELATIONSHIP_KEYS, _apply_migration_008),
    (MIGRATION_009_INVOICE_FILING_OWNER, _apply_migration_009),
    (MIGRATION_010_INVOICE_PRIOR_BALANCE_SNAPSHOTS, _apply_migration_010),
    (MIGRATION_011_PAYMENT_RECEIPTS, _apply_migration_011),
    (MIGRATION_012_INSURANCE_CODING, _apply_migration_012),
    (MIGRATION_013_SYNC_STATE_HARDENING, _apply_migration_013),
    (MIGRATION_014_CANDIDATE_IDENTITY_ALIASES, _apply_migration_014),
    (MIGRATION_015_DUPLICATE_REPAIR_REVERSAL_STATE, _apply_migration_015),
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
                _backup_sqlite_database(backup_path, db_path)
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
    add_columns(
        conn,
        "business_profile",
        {
            "insurance_ein": "TEXT",
            "insurance_npi": "TEXT",
            "insurance_sw": "TEXT",
        },
    )
    add_columns(
        conn,
        "invoices",
        {
            "insurance_coding_included": "INTEGER NOT NULL DEFAULT 0",
            "insurance_diagnosis_code_snapshot": "TEXT",
            "insurance_ein_snapshot": "TEXT",
            "insurance_npi_snapshot": "TEXT",
            "insurance_sw_snapshot": "TEXT",
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
