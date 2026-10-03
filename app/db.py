import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DB_PATH = Path("lead.sqlite3")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with connect() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS search_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS monthly_searches (
                year_month TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS email_sends (
                email_hash TEXT NOT NULL,
                sent_at TEXT NOT NULL
            )"""
        )


def get_cached_search(cache_key: str, *, now: datetime | None = None) -> list[dict[str, Any]] | None:
    current_time = now or datetime.now(UTC)
    cutoff = (current_time - timedelta(hours=24)).isoformat()
    with connect() as connection:
        row = connection.execute(
            "SELECT payload, created_at FROM search_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        if row["created_at"] < cutoff:
            connection.execute("DELETE FROM search_cache WHERE cache_key = ?", (cache_key,))
            return None
        return json.loads(row["payload"])


def set_cached_search(
    cache_key: str, payload: list[dict[str, Any]], *, now: datetime | None = None
) -> None:
    created_at = (now or datetime.now(UTC)).isoformat()
    with connect() as connection:
        connection.execute(
            """INSERT INTO search_cache (cache_key, payload, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload,
               created_at = excluded.created_at""",
            (cache_key, json.dumps(payload), created_at),
        )


def monthly_search_count(*, year_month: str | None = None) -> int:
    month = year_month or datetime.now(UTC).strftime("%Y-%m")
    with connect() as connection:
        row = connection.execute(
            "SELECT count FROM monthly_searches WHERE year_month = ?", (month,)
        ).fetchone()
        return int(row["count"]) if row else 0


def increment_monthly_searches(*, year_month: str | None = None) -> int:
    month = year_month or datetime.now(UTC).strftime("%Y-%m")
    with connect() as connection:
        connection.execute(
            """INSERT INTO monthly_searches (year_month, count) VALUES (?, 1)
               ON CONFLICT(year_month) DO UPDATE SET count = count + 1""",
            (month,),
        )
        row = connection.execute(
            "SELECT count FROM monthly_searches WHERE year_month = ?", (month,)
        ).fetchone()
        return int(row["count"])
