#!/usr/bin/env python3
"""Ingest Codex session JSONL files into a global SQLite usage database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
STATE_DIR = APP_DIR / "state"
DB_PATH = STATE_DIR / "usage.db"
SESSION_ROOT = Path.home() / ".codex" / "sessions"


def compact_text(value: str | None, limit: int = 400) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def extract_response_item_text(payload: dict[str, Any]) -> str | None:
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    parts = payload.get("content")
    if not isinstance(parts, list):
        return None
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "input_text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    if not texts:
        return None
    return compact_text(" ".join(texts))


def project_name_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    return Path(cwd).name or None


def nested_get(payload: dict[str, Any] | None, *keys: str) -> Any:
    current: Any = payload or {}
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


@dataclass
class TurnRecord:
    turn_id: str
    thread_id: str | None = None
    session_file: str | None = None
    cwd: str | None = None
    project_name: str | None = None
    prompt: str | None = None
    model: str | None = None
    started_at: int | None = None
    started_timestamp: str | None = None
    completed_at: int | None = None
    completed_timestamp: str | None = None
    duration_ms: int | None = None
    time_to_first_token_ms: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None
    primary_used_percent: float | None = None
    secondary_used_percent: float | None = None
    primary_window_minutes: int | None = None
    secondary_window_minutes: int | None = None
    primary_resets_at: int | None = None
    secondary_resets_at: int | None = None
    plan_type: str | None = None
    last_agent_message: str | None = None
    completed: bool = False
    latest_token_count_at: str | None = None
    event_order: int = 0


def connect_db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode = wal")
    conn.execute(
        """
        create table if not exists source_files (
            path text primary key,
            size_bytes integer not null,
            mtime_ns integer not null,
            last_ingested_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists turns (
            turn_id text primary key,
            thread_id text,
            session_file text,
            cwd text,
            project_name text,
            prompt text,
            model text,
            started_at integer,
            started_timestamp text,
            completed_at integer,
            completed_timestamp text,
            duration_ms integer,
            time_to_first_token_ms integer,
            input_tokens integer,
            cached_input_tokens integer,
            output_tokens integer,
            reasoning_output_tokens integer,
            total_tokens integer,
            primary_used_percent real,
            secondary_used_percent real,
            primary_window_minutes integer,
            secondary_window_minutes integer,
            primary_resets_at integer,
            secondary_resets_at integer,
            plan_type text,
            last_agent_message text,
            latest_token_count_at text
        )
        """
    )
    conn.execute(
        "create index if not exists idx_turns_completed_timestamp on turns(completed_timestamp desc)"
    )
    conn.execute("create index if not exists idx_turns_project_name on turns(project_name)")
    conn.execute("create index if not exists idx_turns_model on turns(model)")
    return conn


def ingest_token_count(record: TurnRecord, payload: dict[str, Any], timestamp: str | None) -> None:
    info = payload.get("info") or {}
    last_usage = info.get("last_token_usage") or {}
    rate_limits = payload.get("rate_limits") or {}
    record.input_tokens = last_usage.get("input_tokens")
    record.cached_input_tokens = last_usage.get("cached_input_tokens")
    record.output_tokens = last_usage.get("output_tokens")
    record.reasoning_output_tokens = last_usage.get("reasoning_output_tokens")
    record.total_tokens = last_usage.get("total_tokens")
    record.primary_used_percent = nested_get(rate_limits, "primary", "used_percent")
    record.secondary_used_percent = nested_get(rate_limits, "secondary", "used_percent")
    record.primary_window_minutes = nested_get(rate_limits, "primary", "window_minutes")
    record.secondary_window_minutes = nested_get(rate_limits, "secondary", "window_minutes")
    record.primary_resets_at = nested_get(rate_limits, "primary", "resets_at")
    record.secondary_resets_at = nested_get(rate_limits, "secondary", "resets_at")
    record.plan_type = rate_limits.get("plan_type")
    record.latest_token_count_at = timestamp


def parse_session_file(path: Path) -> list[TurnRecord]:
    records: dict[str, TurnRecord] = {}
    ordered_turn_ids: list[str] = []
    current_turn_id: str | None = None

    with path.open("r", encoding="utf-8") as handle:
        for event_order, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            item_type = item.get("type")
            payload = item.get("payload") or {}
            timestamp = item.get("timestamp")

            if item_type == "event_msg" and payload.get("type") == "task_started":
                turn_id = payload.get("turn_id")
                if not turn_id:
                    continue
                current_turn_id = turn_id
                if turn_id not in records:
                    records[turn_id] = TurnRecord(turn_id=turn_id, session_file=str(path))
                    ordered_turn_ids.append(turn_id)
                record = records[turn_id]
                record.started_at = payload.get("started_at")
                record.started_timestamp = timestamp
                record.event_order = event_order
                continue

            if item_type == "turn_context":
                turn_id = payload.get("turn_id")
                if not turn_id:
                    continue
                current_turn_id = turn_id
                if turn_id not in records:
                    records[turn_id] = TurnRecord(turn_id=turn_id, session_file=str(path))
                    ordered_turn_ids.append(turn_id)
                record = records[turn_id]
                record.cwd = payload.get("cwd")
                record.project_name = project_name_from_cwd(record.cwd)
                record.model = payload.get("model") or record.model
                record.event_order = event_order
                continue

            if item_type == "response_item":
                if current_turn_id in records:
                    prompt = extract_response_item_text(payload)
                    if prompt:
                        record = records[current_turn_id]
                        if not record.prompt:
                            record.prompt = prompt
                continue

            if item_type == "event_msg" and payload.get("type") == "user_message":
                if current_turn_id in records:
                    record = records[current_turn_id]
                    if not record.prompt:
                        record.prompt = compact_text(payload.get("message")) or record.prompt
                continue

            if item_type == "event_msg" and payload.get("type") == "token_count":
                if current_turn_id in records:
                    ingest_token_count(records[current_turn_id], payload, timestamp)
                continue

            if item_type == "event_msg" and payload.get("type") == "task_complete":
                turn_id = payload.get("turn_id")
                if not turn_id:
                    continue
                current_turn_id = turn_id
                if turn_id not in records:
                    records[turn_id] = TurnRecord(turn_id=turn_id, session_file=str(path))
                    ordered_turn_ids.append(turn_id)
                record = records[turn_id]
                record.completed_at = payload.get("completed_at")
                record.completed_timestamp = timestamp
                record.duration_ms = payload.get("duration_ms")
                record.time_to_first_token_ms = payload.get("time_to_first_token_ms")
                record.last_agent_message = compact_text(payload.get("last_agent_message"))
                record.completed = True
                record.event_order = event_order
                continue

    return [records[turn_id] for turn_id in ordered_turn_ids if records[turn_id].completed]


def upsert_turn(conn: sqlite3.Connection, record: TurnRecord) -> None:
    conn.execute(
        """
        insert into turns (
            turn_id, thread_id, session_file, cwd, project_name, prompt, model,
            started_at, started_timestamp, completed_at, completed_timestamp,
            duration_ms, time_to_first_token_ms, input_tokens, cached_input_tokens,
            output_tokens, reasoning_output_tokens, total_tokens,
            primary_used_percent, secondary_used_percent, primary_window_minutes,
            secondary_window_minutes, primary_resets_at, secondary_resets_at,
            plan_type, last_agent_message, latest_token_count_at
        ) values (
            :turn_id, :thread_id, :session_file, :cwd, :project_name, :prompt, :model,
            :started_at, :started_timestamp, :completed_at, :completed_timestamp,
            :duration_ms, :time_to_first_token_ms, :input_tokens, :cached_input_tokens,
            :output_tokens, :reasoning_output_tokens, :total_tokens,
            :primary_used_percent, :secondary_used_percent, :primary_window_minutes,
            :secondary_window_minutes, :primary_resets_at, :secondary_resets_at,
            :plan_type, :last_agent_message, :latest_token_count_at
        )
        on conflict(turn_id) do update set
            thread_id=excluded.thread_id,
            session_file=excluded.session_file,
            cwd=excluded.cwd,
            project_name=excluded.project_name,
            prompt=excluded.prompt,
            model=excluded.model,
            started_at=excluded.started_at,
            started_timestamp=excluded.started_timestamp,
            completed_at=excluded.completed_at,
            completed_timestamp=excluded.completed_timestamp,
            duration_ms=excluded.duration_ms,
            time_to_first_token_ms=excluded.time_to_first_token_ms,
            input_tokens=excluded.input_tokens,
            cached_input_tokens=excluded.cached_input_tokens,
            output_tokens=excluded.output_tokens,
            reasoning_output_tokens=excluded.reasoning_output_tokens,
            total_tokens=excluded.total_tokens,
            primary_used_percent=excluded.primary_used_percent,
            secondary_used_percent=excluded.secondary_used_percent,
            primary_window_minutes=excluded.primary_window_minutes,
            secondary_window_minutes=excluded.secondary_window_minutes,
            primary_resets_at=excluded.primary_resets_at,
            secondary_resets_at=excluded.secondary_resets_at,
            plan_type=excluded.plan_type,
            last_agent_message=excluded.last_agent_message,
            latest_token_count_at=excluded.latest_token_count_at
        """,
        record.__dict__,
    )


def file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def should_ingest(conn: sqlite3.Connection, path: Path) -> bool:
    row = conn.execute(
        "select size_bytes, mtime_ns from source_files where path = ?",
        (str(path),),
    ).fetchone()
    size_bytes, mtime_ns = file_signature(path)
    if row is None:
        return True
    return row["size_bytes"] != size_bytes or row["mtime_ns"] != mtime_ns


def mark_ingested(conn: sqlite3.Connection, path: Path) -> None:
    size_bytes, mtime_ns = file_signature(path)
    conn.execute(
        """
        insert into source_files(path, size_bytes, mtime_ns, last_ingested_at)
        values(?, ?, ?, datetime('now'))
        on conflict(path) do update set
            size_bytes=excluded.size_bytes,
            mtime_ns=excluded.mtime_ns,
            last_ingested_at=excluded.last_ingested_at
        """,
        (str(path), size_bytes, mtime_ns),
    )


def iter_session_files() -> list[Path]:
    if not SESSION_ROOT.exists():
        return []
    return sorted(SESSION_ROOT.glob("**/*.jsonl"))


def ingest_all() -> tuple[int, int]:
    conn = connect_db()
    scanned = 0
    written = 0
    try:
        for path in iter_session_files():
            scanned += 1
            if not should_ingest(conn, path):
                continue
            for record in parse_session_file(path):
                upsert_turn(conn, record)
                written += 1
            mark_ingested(conn, path)
        conn.commit()
    finally:
        conn.close()
    return scanned, written


def main() -> int:
    scanned, written = ingest_all()
    print(f"Database: {DB_PATH}")
    print(f"Scanned session files: {scanned}")
    print(f"Upserted completed turns: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
