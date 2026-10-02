import sqlite3
from pathlib import Path

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
