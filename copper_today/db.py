"""SQLite storage: one metrics row per day, plus press-release markers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

COLUMNS = (
    ("post_count", "INTEGER"),
    ("impression_sum", "INTEGER"),
    ("impression_median", "REAL"),
    ("posts_sampled", "INTEGER"),
    ("trends_interest", "REAL"),
    ("copper_close", "REAL"),
    ("x_counts_ok", "INTEGER"),
    ("x_search_ok", "INTEGER"),
    ("collected_at", "TEXT"),
    ("note", "TEXT"),
)

ALLOWED_FIELDS = frozenset(name for name, _typ in COLUMNS)
CLEARABLE = frozenset({"trends_interest"})


class ValidationError(ValueError):
    pass


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            post_count INTEGER,
            impression_sum INTEGER,
            impression_median REAL,
            posts_sampled INTEGER,
            trends_interest REAL,
            copper_close REAL,
            x_counts_ok INTEGER,
            x_search_ok INTEGER,
            collected_at TEXT,
            note TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS press_releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(date, label)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_metrics)")}
    for name, column_type in COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {name} {column_type}")


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_day(conn: sqlite3.Connection, day: str, fields: dict) -> None:
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Unknown daily metric fields: {', '.join(sorted(unknown))}")
    if not fields:
        return
    datetime.strptime(day, "%Y-%m-%d")
    columns = list(fields)
    placeholders = ", ".join("?" for _ in ["date", *columns])
    assignments = ", ".join(f"{column}=excluded.{column}" for column in columns)
    sql = (
        f"INSERT INTO daily_metrics (date, {', '.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(date) DO UPDATE SET {assignments}"
    )
    conn.execute(sql, [day, *[fields[column] for column in columns]])


def clear_column(conn: sqlite3.Connection, column: str) -> None:
    if column not in CLEARABLE:
        raise ValueError(f"Refusing to clear column {column!r}.")
    conn.execute(f"UPDATE daily_metrics SET {column} = NULL")


def get_day(conn: sqlite3.Connection, day: str) -> dict | None:
    row = conn.execute("SELECT * FROM daily_metrics WHERE date = ?", (day,)).fetchone()
    return dict(row) if row else None


def load_days(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM daily_metrics ORDER BY date").fetchall()
    return [dict(row) for row in rows]


def delete_all_metrics(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM daily_metrics")


def has_metrics(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone() is not None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def is_demo(conn: sqlite3.Connection) -> bool:
    return get_meta(conn, "data_origin") == "demo"


def add_press_release(conn: sqlite3.Connection, day: str, label: str) -> int:
    datetime.strptime(day, "%Y-%m-%d")
    cleaned = " ".join(label.split())
    if not cleaned:
        raise ValidationError("Enter a label for the press release.")
    if len(cleaned) > 80:
        raise ValidationError("Keep the label to 80 characters.")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cursor = conn.execute(
        "INSERT INTO press_releases (date, label, created_at) VALUES (?, ?, ?)",
        (day, cleaned, created),
    )
    return int(cursor.lastrowid)


def list_press_releases(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, date, label, created_at FROM press_releases ORDER BY date DESC, id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def delete_press_release(conn: sqlite3.Connection, release_id: int) -> None:
    conn.execute("DELETE FROM press_releases WHERE id = ?", (int(release_id),))
