import sqlite3
from pathlib import Path

import pytest

from copper_today.db import (
    ValidationError,
    add_press_release,
    clear_column,
    connect,
    delete_press_release,
    get_day,
    list_press_releases,
    upsert_day,
)


def test_upsert_does_not_clobber_other_columns(tmp_path: Path):
    with connect(tmp_path / "copper.db") as conn:
        upsert_day(
            conn,
            "2026-11-10",
            {"post_count": 5, "note": "it's a quote; DROP TABLE daily_metrics;"},
        )
        upsert_day(conn, "2026-11-10", {"copper_close": 6.1})
        row = get_day(conn, "2026-11-10")
        assert row["post_count"] == 5
        assert row["copper_close"] == 6.1
        assert "DROP TABLE" in row["note"]
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'daily_metrics'"
        ).fetchone()


def test_clear_trends_rejects_other_columns(tmp_path: Path):
    with connect(tmp_path / "copper.db") as conn:
        upsert_day(conn, "2026-11-10", {"post_count": 5, "trends_interest": 40})
        clear_column(conn, "trends_interest")
        row = get_day(conn, "2026-11-10")
        assert row["post_count"] == 5
        assert row["trends_interest"] is None
        with pytest.raises(ValueError):
            clear_column(conn, "post_count; DROP TABLE daily_metrics")


def test_unknown_field_is_rejected(tmp_path: Path):
    with connect(tmp_path / "copper.db") as conn:
        with pytest.raises(ValueError):
            upsert_day(conn, "2026-11-10", {"nope": 1})


def test_press_release_round_trip(tmp_path: Path):
    with connect(tmp_path / "copper.db") as conn:
        with pytest.raises(ValidationError):
            add_press_release(conn, "2026-11-10", "   ")
        release_id = add_press_release(conn, "2026-11-10", "  Mill update  ")
        with pytest.raises(sqlite3.IntegrityError):
            add_press_release(conn, "2026-11-10", "Mill update")
        releases = list_press_releases(conn)
        assert releases[0]["id"] == release_id
        assert releases[0]["label"] == "Mill update"
        delete_press_release(conn, release_id)
        assert list_press_releases(conn) == []
