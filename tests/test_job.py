from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from copper_today.analytics import prepare
from copper_today.db import connect, get_day, has_metrics, is_demo, upsert_day
from copper_today.demo import build_demo_rows
from copper_today.job import JobError, main, run_job
from copper_today.x_client import SourceError

NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)


def _trends():
    return {"2026-11-01": 10.0, "2026-11-10": 40.0}, None


def _futures():
    return {"2026-11-10": 5.5, "2026-11-11": 5.6}, None


def _posts():
    return [
        {"public_metrics": {"impression_count": 10}},
        {"public_metrics": {"impression_count": 30}},
    ]


def test_live_snapshot_merges_sources_and_drops_stale_trends(tmp_path: Path):
    database = tmp_path / "copper.db"
    with connect(database) as conn:
        upsert_day(conn, "2026-08-01", {"trends_interest": 80, "copper_close": 4.2})
        upsert_day(conn, "2026-11-11", {"copper_close": 9.9})
    result = run_job(
        database,
        now=NOW,
        token="test-token",
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=lambda day, moment: 123,
        search_fetcher=lambda day, moment: _posts(),
    )
    assert result.date == "2026-11-10"
    assert result.exit_code() == 0
    with connect(database) as conn:
        old = get_day(conn, "2026-08-01")
        latest = get_day(conn, "2026-11-10")
        session = get_day(conn, "2026-11-11")
        assert is_demo(conn) is False
    assert old["trends_interest"] is None
    assert old["copper_close"] == 4.2
    assert latest["trends_interest"] == 40.0
    assert latest["copper_close"] == 5.5
    assert latest["post_count"] == 123
    assert latest["impression_sum"] == 40
    assert latest["impression_median"] == 20
    assert latest["posts_sampled"] == 2
    assert latest["x_counts_ok"] == 1
    assert latest["x_search_ok"] == 1
    assert session is None
    assert latest["collected_at"] == "2026-11-11T18:00:00Z"


def test_second_run_does_not_call_x_unless_forced(tmp_path: Path):
    database = tmp_path / "copper.db"
    calls = {"count": 0, "search": 0}

    def count(day, moment):
        calls["count"] += 1
        return 5

    def search(day, moment):
        calls["search"] += 1
        return [{"public_metrics": {"impression_count": 8}}]

    kwargs = dict(
        now=NOW,
        token="test-token",
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=count,
        search_fetcher=search,
    )
    run_job(database, **kwargs)
    run_job(database, **kwargs)
    assert calls == {"count": 1, "search": 1}
    run_job(database, force_x=True, **kwargs)
    assert calls == {"count": 2, "search": 2}


def test_failed_search_is_retried_without_recounting(tmp_path: Path):
    database = tmp_path / "copper.db"
    state = {"count": 0, "search": 0}

    def count(day, moment):
        state["count"] += 1
        return 4

    def search(day, moment):
        state["search"] += 1
        if state["search"] == 1:
            raise SourceError("timeout")
        return [{"public_metrics": {"impression_count": 9}}]

    kwargs = dict(
        now=NOW,
        token="test-token",
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=count,
        search_fetcher=search,
    )
    first = run_job(database, **kwargs)
    assert first.exit_code() == 1
    second = run_job(database, **kwargs)
    assert second.exit_code() == 0
    assert state == {"count": 1, "search": 2}
    with connect(database) as conn:
        row = get_day(conn, "2026-11-10")
    assert row["post_count"] == 4
    assert row["impression_sum"] == 9


def test_missing_token_is_an_error_and_skip_x_is_not(tmp_path: Path):
    database = tmp_path / "copper.db"
    called = {"count": 0}

    def count(day, moment):
        called["count"] += 1
        return 1

    missing = run_job(
        database,
        now=NOW,
        token="",
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=count,
    )
    assert missing.exit_code() == 1
    assert any(step.name == "x" and step.status == "error" for step in missing.steps)
    with connect(database) as conn:
        row = get_day(conn, "2026-11-10")
    assert row["post_count"] is None
    assert row["note"] == "No X_BEARER_TOKEN in the environment."
    assert called["count"] == 0

    skipped = run_job(
        database,
        now=NOW,
        token="",
        skip_x=True,
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=count,
    )
    assert skipped.exit_code() == 0
    assert any(step.name == "x" and step.status == "skipped" for step in skipped.steps)
    assert called["count"] == 0
    with connect(database) as conn:
        kept = get_day(conn, "2026-11-10")
    assert kept["note"] == "No X_BEARER_TOKEN in the environment."


def test_old_date_does_not_call_x(tmp_path: Path):
    database = tmp_path / "copper.db"
    called = {"count": 0}

    def count(day, moment):
        called["count"] += 1
        return 1

    result = run_job(
        database,
        day=date(2026, 1, 1),
        now=NOW,
        token="test-token",
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=count,
    )
    assert result.exit_code() == 0
    assert called["count"] == 0
    assert any("outside" in step.detail for step in result.steps)


def test_future_date_is_rejected_before_fetching(tmp_path: Path):
    def explode():
        raise AssertionError("fetcher should not run")

    with pytest.raises(JobError):
        run_job(
            tmp_path / "copper.db",
            day=date(2026, 11, 12),
            now=NOW,
            trends_fetcher=explode,
            futures_fetcher=explode,
        )


def test_demo_is_labeled_and_a_failed_live_run_keeps_it(tmp_path: Path):
    database = tmp_path / "copper.db"
    demo = run_job(database, demo=True, now=NOW)
    assert demo.exit_code() == 0
    with connect(database) as conn:
        assert is_demo(conn)
        assert has_metrics(conn)
    failed = run_job(
        database,
        now=NOW,
        token="",
        trends_fetcher=lambda: ({}, "down"),
        futures_fetcher=lambda: ({}, "down"),
    )
    assert any(step.name == "database" and step.status == "skipped" for step in failed.steps)
    with connect(database) as conn:
        assert is_demo(conn)
        assert has_metrics(conn)


def test_demo_refuses_to_replace_live_rows(tmp_path: Path):
    database = tmp_path / "copper.db"
    run_job(
        database,
        now=NOW,
        token="",
        skip_x=True,
        trends_fetcher=_trends,
        futures_fetcher=_futures,
    )
    with pytest.raises(JobError):
        run_job(database, demo=True, now=NOW)
    with connect(database) as conn:
        assert get_day(conn, "2026-11-10")["copper_close"] == 5.5
        assert is_demo(conn) is False
    replaced = run_job(database, demo=True, now=NOW, replace_live=True)
    assert replaced.exit_code() == 0
    with connect(database) as conn:
        assert is_demo(conn)


def test_demo_rows_flag_the_final_day():
    end = date(2026, 11, 10)
    result = prepare(build_demo_rows(end))
    last = result.frame.iloc[-1]
    assert last["index_value"] == pytest.approx(100)
    assert last["index_components"] == 5
    assert last["elevated"]
    assert last["rising"]
    assert result.frame["rising"].sum() < len(result.frame) - 1
    assert result.frame["index_value"].dropna().between(0, 100).all()


def test_cli_demo_and_future_date(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("COPPER_DB_PATH", str(tmp_path / "copper.db"))
    assert main(["--demo"]) == 0
    assert "synthetic" in capsys.readouterr().out
    assert main(["--date", "2999-01-01"]) == 2
    assert "future" in capsys.readouterr().err.lower()


def test_note_survives_a_later_skip(tmp_path: Path):
    database = tmp_path / "copper.db"
    posts = [
        {"public_metrics": {"impression_count": 10}},
        {"public_metrics": {}},
    ]
    run_job(
        database,
        now=NOW,
        token="test-token",
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=lambda day, moment: 3,
        search_fetcher=lambda day, moment: posts,
    )
    run_job(
        database,
        now=NOW + timedelta(minutes=5),
        token="test-token",
        trends_fetcher=_trends,
        futures_fetcher=_futures,
        count_fetcher=lambda day, moment: (_ for _ in ()).throw(AssertionError("recounted")),
        search_fetcher=lambda day, moment: (_ for _ in ()).throw(AssertionError("researched")),
    )
    with connect(database) as conn:
        row = get_day(conn, "2026-11-10")
    assert row["note"] == "1 post had no impression_count"
