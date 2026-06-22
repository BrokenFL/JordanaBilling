from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import connect, init_db
from .backfill import backfill_phase2
from .google_sync import SyncError, cli_sync_status, load_config, sync_now
from .importer import import_csv
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

    report_parser = subparsers.add_parser(
        "report",
        help="Print an acceptance report for an import run.",
    )
    report_parser.add_argument("import_run_id")

    sync_parser = subparsers.add_parser(
        "sync",
        help="Pull completed calendar snapshots from Google Apps Script.",
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
        conn = connect(args.db)
        init_db(conn)
        print(f"Initialized {args.db}")
        return 0

    if args.command == "import-csv":
        conn = connect(args.db)
        import_run_id = import_csv(conn, args.csv_path, args.source_name)
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
            result = sync_now(
                config,
                full=args.full,
                dry_run=args.dry_run,
            )
        except SyncError as error:
            print(f"sync failed: {error}")
            return 1
        print(
            "sync complete "
            f"rows_fetched={result.rows_fetched} "
            f"rows_imported={result.rows_imported} "
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
        conn = connect(args.db)
        init_db(conn)
        rule_id = seed_rate_rule(
            conn,
            amount_cents=dollars_to_cents(args.amount),
            effective_from=args.effective_from,
            duration_minutes=args.duration_minutes,
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
        conn = connect(args.db)
        init_db(conn)
        set_rate_policy(conn, args.policy_name, args.policy_value)
        conn.commit()
        print(f"{args.policy_name}={args.policy_value}")
        return 0

    if args.command == "record-review":
        conn = connect(args.db)
        init_db(conn)
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

    if args.command == "normalize-existing":
        conn = connect(args.db)
        init_db(conn)
        updated = backfill_phase2(conn)
        conn.commit()
        print(f"normalized_existing={updated}")
        return 0

    if args.command == "serve-review":
        serve_review(args.db, args.host, args.port)
        return 0

    if args.command == "set-business-profile":
        conn = connect(args.db)
        init_db(conn)
        profile = save_business_profile(conn, json.loads(Path(args.json_path).read_text(encoding="utf-8")))
        conn.close()
        print(f"business_profile_id={profile['business_profile_id']}")
        return 0

    if args.command == "list-services":
        conn = connect(args.db)
        init_db(conn)
        for service in list_services(conn, args.include_inactive):
            print(f"{service['service_catalog_id']}\t{service['display_name']}\t{'active' if service['active'] else 'inactive'}")
        conn.close()
        return 0

    if args.command == "set-service-active":
        conn = connect(args.db)
        init_db(conn)
        service = set_service_active(conn, args.service_catalog_id, not args.inactive)
        conn.close()
        print(f"{service['display_name']}={'active' if service['active'] else 'inactive'}")
        return 0

    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
