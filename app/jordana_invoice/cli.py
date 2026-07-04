from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import (
    OperationalDatabaseError,
    OperationalImportAuthorization,
    authorize_operational_import,
    connect,
    is_operational_db_path,
    migrate_database,
)
from .backfill import backfill_phase2
from .google_sync import (
    SyncError,
    cli_sync_status,
    load_config,
    load_env_file,
    sync_calendar_automatically,
    sync_with_process_lock,
)
from .importer import import_csv, replay_existing_raw_snapshots
from .duplicate_repair import duplicate_repair_plan, reverse_duplicate_repair
from .rates import dollars_to_cents, seed_rate_rule, set_rate_policy
from .report import acceptance_report
from .review import record_review_decision
from .review_server import serve as serve_review
from .invoice_services import save_business_profile
from .service_catalog import list_services, set_service_active


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jordana-invoice",
        description="Local-first Jordana calendar invoice normalization tools.",
    )
    parser.add_argument(
        "--db",
        default="data/jordana_invoice.sqlite3",
        help="Path to the local SQLite database.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or migrate the local database.")

    import_parser = subparsers.add_parser(
        "import-csv",
        help="Import a Google Sheets CSV export of Raw_Event_Snapshots.",
    )
    import_parser.add_argument("csv_path")
    import_parser.add_argument("--source-name")
    import_parser.add_argument(
        "--report",
        default=None,
        help="Optional Markdown file to write the acceptance report.",
    )
    import_parser.add_argument(
        "--allow-operational-db",
        action="store_true",
        default=False,
        help=(
            "Bypass the operational-database safety guard.  "
            "Required when --db points to the live application database "
            "(e.g. data/jordana_invoice.sqlite3).  "
            "Do NOT use for routine acceptance testing — use "
            "scripts/run_acceptance_test.sh instead."
        ),
    )
    import_parser.add_argument(
        "--confirm-operational-db-path",
        default=None,
        help=(
            "Explicit confirmation of the operational database canonical path. "
            "Required when --allow-operational-db is set. "
            "Must resolve exactly to the configured operational database path."
        ),
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Print an acceptance report for an import run.",
    )
    report_parser.add_argument("import_run_id")

    sync_parser = subparsers.add_parser(
        "sync",
        help="Pull calendar snapshots from Google Apps Script.",
    )
    sync_parser.add_argument(
        "--full",
        action="store_true",
        help="Request all available remote rows from the beginning.",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and validate remote rows without writing locally.",
    )
    sync_parser.add_argument(
        "--env",
        default=".env",
        help="Path to the environment file.",
    )

    status_parser = subparsers.add_parser(
        "sync-status",
        help="Show Google Apps Script sync status.",
    )
    status_parser.add_argument(
        "--env",
        default=".env",
        help="Path to the environment file.",
    )

    rate_parser = subparsers.add_parser(
        "seed-rate-rule",
        help="Add an effective-dated rate rule for testing normalization.",
    )
    rate_parser.add_argument("--amount", required=True, help="Dollar amount, for example 150 or 150.00.")
    rate_parser.add_argument("--effective-from", required=True)
    rate_parser.add_argument("--duration-minutes", type=int)
    rate_parser.add_argument("--billing-session-type")
    rate_parser.add_argument("--service-mode")
    rate_parser.add_argument("--rate-group")
    rate_parser.add_argument("--time-category", default="standard")
    rate_parser.add_argument("--account-id")
    rate_parser.add_argument("--person-id")
    rate_parser.add_argument("--priority", type=int, default=100)

    policy_parser = subparsers.add_parser(
        "set-rate-policy",
        help="Set a backend rate policy such as weekend_evening_policy.",
    )
    policy_parser.add_argument("policy_name")
    policy_parser.add_argument("policy_value")

    review_parser = subparsers.add_parser(
        "record-review",
        help="Record a simple developer-facing review status decision.",
    )
    review_parser.add_argument("--candidate-id")
    review_parser.add_argument("--session-id")
    review_parser.add_argument("--status", required=True)
    review_parser.add_argument("--reason", default="")

    subparsers.add_parser(
        "normalize-existing",
        help="Backfill Phase 2 normalization fields for existing imported rows.",
    )

    duplicate_parser = subparsers.add_parser(
        "duplicate-repair",
        help="Analyze duplicate calendar candidates and produce a sanitized repair plan.",
    )
    duplicate_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Analyze only and perform no writes. This is the default.",
    )
    duplicate_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply safe duplicate reconciliation actions. Requires --confirm-apply.",
    )
    duplicate_parser.add_argument(
        "--confirm-apply",
        default="",
        help="Must be exactly APPLY_DUPLICATE_REPAIR when --apply is used.",
    )
    duplicate_parser.add_argument(
        "--reverse",
        action="store_true",
        help="Safely reverse applied duplicate repair changes. Requires --confirm-reversal.",
    )
    duplicate_parser.add_argument(
        "--confirm-reversal",
        default="",
        help="Must be exactly REVERSE_DUPLICATE_REPAIR when --reverse is used.",
    )

    reconcile_parser = subparsers.add_parser(
        "calendar-reconcile",
        help=(
            "Replay preserved raw calendar snapshots through the review model "
            "without inserting duplicate raw evidence."
        ),
    )
    reconcile_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Analyze only and perform no durable writes. This is the default.",
    )
    reconcile_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply replay recovery. Requires --confirm-apply.",
    )
    reconcile_parser.add_argument(
        "--confirm-apply",
        default="",
        help="Must be exactly APPLY_CALENDAR_RECONCILE when --apply is used.",
    )

    serve_parser = subparsers.add_parser(
        "serve-review",
        help="Run the local review UI.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)

    profile_parser = subparsers.add_parser(
        "set-business-profile",
        help="Create or update the private local business profile from an ignored JSON file.",
    )
    profile_parser.add_argument("json_path")

    services_parser = subparsers.add_parser("list-services", help="List invoice service catalog entries.")
    services_parser.add_argument("--include-inactive", action="store_true")

    service_status_parser = subparsers.add_parser("set-service-active", help="Activate or deactivate a service catalog entry.")
    service_status_parser.add_argument("service_catalog_id")
    service_status_parser.add_argument("--inactive", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "init-db":
        result = migrate_database(args.db)
        if result["migrated"]:
            print(f"Migrated {args.db}")
            if result["backup_path"]:
                print(f"Backup: {result['backup_path']}")
        else:
            print(f"Schema already current: {args.db}")
        return 0

    if args.command == "import-csv":
        # Load .env so JORDANA_DATABASE_PATH is available for the guard.
        load_env_file()
        is_op = is_operational_db_path(args.db)
        authorization: OperationalImportAuthorization | None = None

        if is_op:
            if not args.allow_operational_db:
                print(
                    f"REFUSED: '{args.db}' is the configured operational database.\n"
                    "Running import-csv against the live database can overwrite or "
                    "corrupt manual review decisions and approved sessions.\n"
                    "\n"
                    "To run acceptance tests safely, use:\n"
                    "  scripts/run_acceptance_test.sh\n"
                    "\n"
                    "If you genuinely need to import into the live database, add:\n"
                    "  --allow-operational-db --confirm-operational-db-path /canonical/path\n"
                    "A verified backup will be created automatically before proceeding.",
                    file=__import__('sys').stderr,
                )
                return 1
            # Validate authorization and create backup BEFORE migration.
            try:
                authorization = authorize_operational_import(
                    args.db,
                    confirmed_path=args.confirm_operational_db_path,
                )
            except OperationalDatabaseError as error:
                print(f"REFUSED: {error}", file=__import__('sys').stderr)
                return 1
            if authorization.backup_path:
                print(f"Backup created: {authorization.backup_path}")

        # Migration runs after backup is verified (or immediately for non-operational).
        migrate_database(args.db)
        conn = connect(args.db)
        try:
            import_run_id = import_csv(
                conn,
                args.csv_path,
                args.source_name,
                operational_authorization=authorization,
            )
        except OperationalDatabaseError as error:
            print(f"REFUSED: {error}", file=__import__('sys').stderr)
            return 1
        report = acceptance_report(conn, import_run_id)
        if args.report:
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report, encoding="utf-8")
        print(import_run_id)
        return 0

    if args.command == "report":
        conn = connect(args.db)
        print(acceptance_report(conn, args.import_run_id))
        return 0

    if args.command == "sync":
        try:
            config = load_config(args.env)
            if args.full:
                result = sync_with_process_lock(config, full=True, dry_run=args.dry_run)
            else:
                result = sync_calendar_automatically(config, dry_run=args.dry_run)
        except SyncError as error:
            print(f"sync failed: {error}")
            return 1
        print(
            "sync complete "
            f"mode={result.mode} "
            f"rows_fetched={result.rows_fetched} "
            f"rows_imported={result.rows_imported} "
            f"duplicate_rows_skipped={result.duplicate_rows_skipped} "
            f"dry_run={result.dry_run}"
        )
        return 0

    if args.command == "sync-status":
        try:
            print(cli_sync_status(load_config(args.env)))
        except SyncError as error:
            print(f"sync-status failed: {error}")
            return 1
        return 0

    if args.command == "seed-rate-rule":
        migrate_database(args.db)
        conn = connect(args.db)
        rule_id = seed_rate_rule(
            conn,
            amount_cents=dollars_to_cents(args.amount),
            effective_from=args.effective_from,
            duration_minutes=args.duration_minutes,
            billing_session_type=args.billing_session_type,
            service_mode=args.service_mode,
            rate_group=args.rate_group,
            time_category=args.time_category,
            client_account_id=args.account_id,
            person_id=args.person_id,
            priority=args.priority,
        )
        conn.commit()
        print(rule_id)
        return 0

    if args.command == "set-rate-policy":
        migrate_database(args.db)
        conn = connect(args.db)
        set_rate_policy(conn, args.policy_name, args.policy_value)
        conn.commit()
        print(f"{args.policy_name}={args.policy_value}")
        return 0

    if args.command == "record-review":
        migrate_database(args.db)
        conn = connect(args.db)
        review_id = record_review_decision(
            conn,
            candidate_id=args.candidate_id,
            session_id=args.session_id,
            review_status=args.status,
            decision_payload={"review_reasons": [args.reason] if args.reason else []},
            reason=args.reason,
        )
        conn.commit()
        print(review_id)
        return 0

    if args.command == "calendar-reconcile":
        if args.apply and args.confirm_apply != "APPLY_CALENDAR_RECONCILE":
            print(
                "REFUSED: --apply requires "
                "--confirm-apply APPLY_CALENDAR_RECONCILE",
                file=__import__('sys').stderr,
            )
            return 1
        migrate_database(args.db)
        conn = connect(args.db)
        try:
            result = replay_existing_raw_snapshots(conn, apply=args.apply)
        finally:
            conn.close()
        print(json.dumps(result.as_dict(), sort_keys=True))
        return 0

    if args.command == "normalize-existing":
        migrate_database(args.db)
        conn = connect(args.db)
        updated = backfill_phase2(conn)
        conn.commit()
        print(f"normalized_existing={updated}")
        return 0

    if args.command == "duplicate-repair":
        migrate_database(args.db)
        conn = connect(args.db)
        try:
            if args.apply and args.reverse:
                print("REFUSED: choose either --apply or --reverse, not both.", file=__import__("sys").stderr)
                return 1
            if args.reverse:
                if args.confirm_reversal != "REVERSE_DUPLICATE_REPAIR":
                    print(
                        "REFUSED: --reverse requires --confirm-reversal REVERSE_DUPLICATE_REPAIR",
                        file=__import__("sys").stderr,
                    )
                    return 1
                result = reverse_duplicate_repair(conn, confirm=True)
                print(json.dumps(result, sort_keys=True))
                return 0
            should_apply = bool(args.apply)
            if should_apply and args.confirm_apply != "APPLY_DUPLICATE_REPAIR":
                print(
                    "REFUSED: --apply requires --confirm-apply APPLY_DUPLICATE_REPAIR",
                    file=__import__("sys").stderr,
                )
                return 1
            result = duplicate_repair_plan(conn, apply=should_apply, confirm=should_apply)
        finally:
            conn.close()
        print(json.dumps(result["summary"], sort_keys=True))
        if not should_apply:
            print("dry_run=true writes_performed=false")
        return 0

    if args.command == "serve-review":
        serve_review(args.db, args.host, args.port)
        return 0

    if args.command == "set-business-profile":
        migrate_database(args.db)
        conn = connect(args.db)
        profile = save_business_profile(conn, json.loads(Path(args.json_path).read_text(encoding="utf-8")))
        conn.close()
        print(f"business_profile_id={profile['business_profile_id']}")
        return 0

    if args.command == "list-services":
        migrate_database(args.db)
        conn = connect(args.db)
        for service in list_services(conn, args.include_inactive):
            print(f"{service['service_catalog_id']}\t{service['display_name']}\t{'active' if service['active'] else 'inactive'}")
        conn.close()
        return 0

    if args.command == "set-service-active":
        migrate_database(args.db)
        conn = connect(args.db)
        service = set_service_active(conn, args.service_catalog_id, not args.inactive)
        conn.close()
        print(f"{service['display_name']}={'active' if service['active'] else 'inactive'}")
        return 0

    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
