#!/usr/bin/env python3
"""Serve a local dashboard for Codex usage records."""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "state" / "usage.db"
INDEX_PATH = APP_DIR / "web" / "index.html"
HOST = "127.0.0.1"
PORT = 8765


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        select
            count(*) as turns,
            count(distinct coalesce(project_name, '-')) as projects,
            count(distinct coalesce(model, '-')) as models,
            coalesce(sum(total_tokens), 0) as total_tokens,
            coalesce(sum(input_tokens), 0) as input_tokens,
            coalesce(sum(cached_input_tokens), 0) as cached_input_tokens,
            coalesce(sum(output_tokens), 0) as output_tokens,
            coalesce(sum(reasoning_output_tokens), 0) as reasoning_output_tokens,
            min(completed_timestamp) as first_seen_at,
            max(completed_timestamp) as last_seen_at
        from turns
        """
    ).fetchone()
    return dict(row)


def fetch_projects(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        select
            coalesce(project_name, '-') as project_name,
            count(*) as turns,
            coalesce(sum(total_tokens), 0) as total_tokens,
            max(completed_timestamp) as last_seen_at
        from turns
        group by coalesce(project_name, '-')
        order by total_tokens desc, project_name asc
        """
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_daily(conn: sqlite3.Connection, days: int) -> list[dict]:
    rows = conn.execute(
        """
        select
            substr(coalesce(completed_timestamp, started_timestamp), 1, 10) as day,
            count(*) as turns,
            coalesce(sum(total_tokens), 0) as total_tokens
        from turns
        where datetime(coalesce(completed_timestamp, started_timestamp)) >= datetime('now', ?)
        group by substr(coalesce(completed_timestamp, started_timestamp), 1, 10)
        order by day asc
        """,
        (f"-{days} days",),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_turns(
    conn: sqlite3.Connection,
    limit: int,
    project_name: str | None,
    model: str | None,
) -> list[dict]:
    where = []
    params: list[object] = []
    if project_name:
        where.append("project_name = ?")
        params.append(project_name)
    if model:
        where.append("model = ?")
        params.append(model)

    query = """
        with ordered_turns as (
            select
                turn_id,
                project_name,
                cwd,
                prompt,
                model,
                completed_timestamp,
                duration_ms,
                time_to_first_token_ms,
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens,
                total_tokens,
                primary_used_percent,
                secondary_used_percent,
                primary_resets_at,
                secondary_resets_at,
                lag(primary_used_percent) over (
                    order by completed_timestamp asc, turn_id asc
                ) as previous_primary_used_percent,
                lag(secondary_used_percent) over (
                    order by completed_timestamp asc, turn_id asc
                ) as previous_secondary_used_percent,
                lag(primary_resets_at) over (
                    order by completed_timestamp asc, turn_id asc
                ) as previous_primary_resets_at,
                lag(secondary_resets_at) over (
                    order by completed_timestamp asc, turn_id asc
                ) as previous_secondary_resets_at
            from turns
        )
        select
            turn_id,
            project_name,
            cwd,
            prompt,
            model,
            completed_timestamp,
            duration_ms,
            time_to_first_token_ms,
            input_tokens,
            cached_input_tokens,
            output_tokens,
            reasoning_output_tokens,
            total_tokens,
            primary_used_percent,
            secondary_used_percent,
            case
                when primary_used_percent is null or previous_primary_used_percent is null then null
                when primary_resets_at != previous_primary_resets_at then null
                when primary_used_percent < previous_primary_used_percent then null
                else round(primary_used_percent - previous_primary_used_percent, 3)
            end as primary_request_percent,
            case
                when secondary_used_percent is null or previous_secondary_used_percent is null then null
                when secondary_resets_at != previous_secondary_resets_at then null
                when secondary_used_percent < previous_secondary_used_percent then null
                else round(secondary_used_percent - previous_secondary_used_percent, 3)
            end as secondary_request_percent
        from ordered_turns
    """
    if where:
        query += " where " + " and ".join(where)
    query += " order by completed_timestamp desc, turn_id desc limit ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/":
            content = INDEX_PATH.read_text(encoding="utf-8")
            body = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/summary":
            self.respond_json(self.with_db(fetch_summary))
            return

        if parsed.path == "/api/projects":
            self.respond_json(self.with_db(fetch_projects))
            return

        if parsed.path == "/api/daily":
            params = parse_qs(parsed.query)
            days = int(params.get("days", ["14"])[0])
            self.respond_json(self.with_db(lambda conn: fetch_daily(conn, days)))
            return

        if parsed.path == "/api/turns":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["50"])[0])
            project_name = params.get("project", [""])[0] or None
            model = params.get("model", [""])[0] or None
            self.respond_json(self.with_db(lambda conn: fetch_turns(conn, limit, project_name, model)))
            return

        self.send_error(404, "Not found")

    def log_message(self, format: str, *args) -> None:
        return

    def with_db(self, fn):
        conn = connect_db()
        try:
            return fn(conn)
        finally:
            conn.close()

    def respond_json(self, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    if not DB_PATH.exists():
        raise SystemExit(
            f"Database not found: {DB_PATH}\nRun collector first: python3 {APP_DIR / 'collector.py'}"
        )
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard: http://{HOST}:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
