from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import connect, init_db
from .review_services import (
    add_account_member,
    approve_candidate,
    create_account,
    create_billing_party,
    create_person,
    create_rate_rule_from_payload,
    dashboard_status,
    get_account_record,
    get_person_record,
    get_review_candidate,
    list_account_records,
    list_people_records,
    list_review_candidates,
    list_rate_rules,
    mark_candidate,
    merge_people,
    refresh_candidate_suggestions,
    save_billing_section,
    save_interpretation,
    save_person_section,
    save_relationship_section,
    save_session_draft,
    search_accounts,
    search_billing_parties,
    search_people,
    update_account,
    update_billing_party,
    update_person,
)


STATIC_DIR = Path(__file__).parent / "static"


def make_handler(database_path: str):
    class ReviewHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path in {"/", "/review"}:
                    self.send_static("review.html")
                    return
                if parsed.path in {"/clients", "/people"} or parsed.path.startswith("/clients/") or parsed.path.startswith("/people/"):
                    self.send_static("review.html")
                    return
                if parsed.path.startswith("/static/"):
                    self.send_static(parsed.path.removeprefix("/static/"))
                    return
                if parsed.path == "/api/status":
                    self.send_json(dashboard_status(self.conn()))
                    return
                if parsed.path == "/api/review/candidates":
                    query = parse_qs(parsed.query)
                    self.send_json(
                        list_review_candidates(
                            self.conn(),
                            query=first(query, "q"),
                            review_status=first(query, "review_status"),
                            service_mode=first(query, "service_mode"),
                            time_category=first(query, "time_category"),
                            payment_status=first(query, "payment_status"),
                            calendar_filter=first(query, "calendar_filter"),
                            limit=int(first(query, "limit") or 25),
                            offset=int(first(query, "offset") or 0),
                        )
                    )
                    return
                if parsed.path.startswith("/api/review/candidates/"):
                    candidate_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(get_review_candidate(self.conn(), candidate_id))
                    return
                if parsed.path == "/api/people":
                    query = parse_qs(parsed.query)
                    if first(query, "full") == "1":
                        self.send_json(list_people_records(self.conn(), first(query, "q")))
                    else:
                        self.send_json(search_people(self.conn(), first(query, "q")))
                    return
                if parsed.path.startswith("/api/people/"):
                    person_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(get_person_record(self.conn(), person_id))
                    return
                if parsed.path == "/api/accounts":
                    query = parse_qs(parsed.query)
                    if first(query, "full") == "1":
                        self.send_json(list_account_records(self.conn(), first(query, "q")))
                    else:
                        self.send_json(search_accounts(self.conn(), first(query, "q")))
                    return
                if parsed.path.startswith("/api/accounts/"):
                    account_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(get_account_record(self.conn(), account_id))
                    return
                if parsed.path == "/api/billing-parties":
                    self.send_json(search_billing_parties(self.conn(), first(parse_qs(parsed.query), "q")))
                    return
                if parsed.path == "/api/rate-rules":
                    self.send_json(list_rate_rules(self.conn()))
                    return
                self.send_error(404)
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=500)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            data = self.read_json()
            try:
                if parsed.path == "/api/people":
                    self.send_json(create_person(self.conn(), data))
                    return
                if parsed.path.startswith("/api/people/") and parsed.path.endswith("/merge"):
                    survivor_id = parsed.path.strip("/").split("/")[2]
                    self.send_json(
                        merge_people(
                            self.conn(),
                            survivor_id,
                            data["duplicate_person_id"],
                            data.get("reason", ""),
                        )
                    )
                    return
                if parsed.path.startswith("/api/people/"):
                    person_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(update_person(self.conn(), person_id, data))
                    return
                if parsed.path == "/api/accounts":
                    self.send_json(create_account(self.conn(), data["account_name"], data.get("account_type", "individual")))
                    return
                if parsed.path.startswith("/api/accounts/"):
                    account_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(update_account(self.conn(), account_id, data))
                    return
                if parsed.path == "/api/billing-parties":
                    self.send_json(create_billing_party(self.conn(), data))
                    return
                if parsed.path.startswith("/api/billing-parties/"):
                    billing_party_id = parsed.path.rsplit("/", 1)[-1]
                    self.send_json(update_billing_party(self.conn(), billing_party_id, data))
                    return
                if parsed.path == "/api/rate-rules":
                    self.send_json(create_rate_rule_from_payload(self.conn(), data))
                    return
                if parsed.path == "/api/account-members":
                    self.send_json(
                        {
                            "account_member_id": add_account_member(
                                self.conn(),
                                data["account_id"],
                                data["person_id"],
                                data.get("relationship_role", "primary"),
                                bool(data.get("is_primary")),
                            )
                        }
                    )
                    return
                if parsed.path.startswith("/api/review/candidates/"):
                    parts = parsed.path.strip("/").split("/")
                    candidate_id = parts[3]
                    action = parts[4] if len(parts) > 4 else "save"
                    if action == "save":
                        self.send_json(save_interpretation(self.conn(), candidate_id, data))
                        return
                    if action == "save-person":
                        self.send_json(save_person_section(self.conn(), candidate_id, data))
                        return
                    if action == "save-relationship":
                        self.send_json(save_relationship_section(self.conn(), candidate_id, data))
                        return
                    if action == "save-billing":
                        self.send_json(save_billing_section(self.conn(), candidate_id, data))
                        return
                    if action == "save-session":
                        self.send_json(save_session_draft(self.conn(), candidate_id, data))
                        return
                    if action == "refresh":
                        refresh_candidate_suggestions(self.conn(), candidate_id)
                        self.send_json(get_review_candidate(self.conn(), candidate_id))
                        return
                    if action == "approve":
                        self.send_json(approve_candidate(self.conn(), candidate_id, data))
                        return
                    if action == "mark":
                        self.send_json(
                            mark_candidate(
                                self.conn(),
                                candidate_id,
                                classification=data.get("classification", "personal"),
                                reason=data.get("reason", ""),
                            )
                        )
                        return
                self.send_error(404)
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)

        def conn(self):
            conn = connect(database_path)
            init_db(conn)
            return conn

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def send_json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_static(self, name: str) -> None:
            path = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() not in path.parents and path != STATIC_DIR.resolve():
                self.send_error(403)
                return
            if not path.exists() or path.is_dir():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ReviewHandler


def first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or [""]
    return values[0]


def serve(database_path: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(database_path))
    print(f"Review UI running at http://{host}:{port}/review")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/jordana_invoice.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    serve(args.db, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
