"""SQLite storage: one metrics row per day, plus press releases, stories, and scores."""

from __future__ import annotations

import json
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sampled_posts (
            date TEXT NOT NULL,
            post_id TEXT NOT NULL,
            text TEXT NOT NULL,
            impression_count INTEGER,
            like_count INTEGER NOT NULL,
            repost_count INTEGER NOT NULL,
            reply_count INTEGER NOT NULL,
            quote_count INTEGER NOT NULL,
            engagement INTEGER NOT NULL,
            PRIMARY KEY (date, post_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_stories (
            date TEXT NOT NULL,
            rank INTEGER NOT NULL,
            headline TEXT NOT NULL,
            summary TEXT NOT NULL,
            category TEXT NOT NULL,
            impressions INTEGER NOT NULL,
            engagement INTEGER NOT NULL,
            unverified INTEGER NOT NULL,
            links_json TEXT NOT NULL,
            PRIMARY KEY (date, rank)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS story_runs (
            date TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            detail TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_scores (
            date TEXT PRIMARY KEY,
            attention_index REAL,
            news_impressions INTEGER,
            news_flow REAL,
            score REAL,
            color TEXT NOT NULL,
            reason TEXT NOT NULL,
            attention_weight REAL NOT NULL,
            news_weight REAL NOT NULL,
            price_change_pct REAL,
            post_count_percentile REAL,
            top_story_impressions INTEGER,
            score_percentile REAL,
            day_type TEXT NOT NULL,
            history_days INTEGER NOT NULL,
            comparison_days INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        )
        """
    )


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


def clear_stories_and_scores(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sampled_posts")
    conn.execute("DELETE FROM daily_stories")
    conn.execute("DELETE FROM story_runs")
    conn.execute("DELETE FROM daily_scores")


def replace_sampled_posts(conn: sqlite3.Connection, day: str, posts: list[dict]) -> None:
    datetime.strptime(day, "%Y-%m-%d")
    conn.execute("DELETE FROM sampled_posts WHERE date = ?", (day,))
    for post in posts:
        conn.execute(
            """
            INSERT INTO sampled_posts (
                date, post_id, text, impression_count, like_count,
                repost_count, reply_count, quote_count, engagement
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                post["post_id"],
                post["text"],
                post["impression_count"],
                int(post["like_count"]),
                int(post["repost_count"]),
                int(post["reply_count"]),
                int(post["quote_count"]),
                int(post["engagement"]),
            ),
        )


def load_sampled_posts(conn: sqlite3.Connection, day: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM sampled_posts
        WHERE date = ?
        ORDER BY engagement DESC, impression_count DESC, post_id
        """,
        (day,),
    ).fetchall()
    return [dict(row) for row in rows]


def replace_stories(conn: sqlite3.Connection, day: str, stories: list[dict]) -> None:
    datetime.strptime(day, "%Y-%m-%d")
    conn.execute("DELETE FROM daily_stories WHERE date = ?", (day,))
    for story in stories:
        conn.execute(
            """
            INSERT INTO daily_stories (
                date, rank, headline, summary, category, impressions,
                engagement, unverified, links_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                int(story["rank"]),
                story["headline"],
                story["summary"],
                story["category"],
                int(story["impressions"]),
                int(story["engagement"]),
                1 if story["unverified"] else 0,
                json.dumps(story["links"], separators=(",", ":")),
            ),
        )


def list_stories(conn: sqlite3.Connection, day: str | None = None) -> list[dict]:
    if day is None:
        rows = conn.execute("SELECT * FROM daily_stories ORDER BY date, rank").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM daily_stories WHERE date = ? ORDER BY rank",
            (day,),
        ).fetchall()
    stories = []
    for row in rows:
        item = dict(row)
        item["links"] = json.loads(item.pop("links_json") or "[]")
        item["unverified"] = bool(item["unverified"])
        stories.append(item)
    return stories


def save_story_run(
    conn: sqlite3.Connection,
    day: str,
    *,
    status: str,
    detail: str | None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
) -> None:
    datetime.strptime(day, "%Y-%m-%d")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO story_runs (
            date, status, detail, model, input_tokens, output_tokens,
            estimated_cost_usd, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            status = excluded.status,
            detail = excluded.detail,
            model = excluded.model,
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            estimated_cost_usd = excluded.estimated_cost_usd,
            created_at = excluded.created_at
        """,
        (day, status, detail, model, input_tokens, output_tokens, estimated_cost_usd, created),
    )


def list_story_runs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM story_runs ORDER BY date").fetchall()
    return [dict(row) for row in rows]


def save_scores(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.execute("DELETE FROM daily_scores")
    for row in rows:
        conn.execute(
            """
            INSERT INTO daily_scores (
                date, attention_index, news_impressions, news_flow, score, color,
                reason, attention_weight, news_weight, price_change_pct,
                post_count_percentile, top_story_impressions, score_percentile,
                day_type, history_days, comparison_days, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["date"],
                row["attention_index"],
                row["news_impressions"],
                row["news_flow"],
                row["score"],
                row["color"],
                row["reason"],
                row["attention_weight"],
                row["news_weight"],
                row["price_change_pct"],
                row["post_count_percentile"],
                row["top_story_impressions"],
                row["score_percentile"],
                row["day_type"],
                int(row["history_days"]),
                int(row["comparison_days"]),
                row["computed_at"],
            ),
        )


def list_scores(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM daily_scores ORDER BY date").fetchall()
    return [dict(row) for row in rows]
