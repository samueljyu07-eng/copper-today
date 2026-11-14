import json

import pandas as pd

from copper_today.score import build_score_rows


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _by_date(rows: list[dict]) -> dict[str, dict]:
    return {row["date"]: row for row in rows}


def test_colour_uses_the_full_window_and_waits_for_seven_days():
    rows = _frame(
        [
            {"date": f"2026-11-{day:02d}", "index_value": value}
            for day, value in zip(
                [2, 3, 4, 5, 6, 9, 10, 11, 12, 13],
                [10, 100, 20, 90, 30, 80, 40, 70, 60, 50],
            )
        ]
    )
    scored = _by_date(build_score_rows(rows, {}, 0.7))
    assert scored["2026-11-06"]["color"] == "calibrating"
    assert scored["2026-11-06"]["history_days"] == 5
    assert "5 of 7 days stored" in scored["2026-11-06"]["reason"]
    assert scored["2026-11-13"]["color"] == "yellow"
    assert scored["2026-11-13"]["score"] == 50

    high = _frame([{"date": f"2026-11-{day:02d}", "index_value": value} for day, value in zip(
        [2, 3, 4, 5, 6, 9, 10, 11, 12, 13],
        [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    )])
    assert _by_date(build_score_rows(high, {}, 1))["2026-11-13"]["color"] == "green"

    low = _frame([{"date": f"2026-11-{day:02d}", "index_value": value} for day, value in zip(
        [2, 3, 4, 5, 6, 9, 10, 11, 12, 13],
        [100, 90, 80, 70, 60, 50, 40, 30, 20, 10],
    )])
    assert _by_date(build_score_rows(low, {}, 1))["2026-11-13"]["color"] == "red"


def test_weekends_are_not_ranked_against_weekdays():
    rows = _frame(
        [
            {"date": "2026-11-01", "index_value": 50},
            {"date": "2026-11-02", "index_value": 80},
            {"date": "2026-11-03", "index_value": 80},
            {"date": "2026-11-04", "index_value": 80},
            {"date": "2026-11-05", "index_value": 80},
            {"date": "2026-11-06", "index_value": 80},
            {"date": "2026-11-07", "index_value": 10},
            {"date": "2026-11-09", "index_value": 80},
            {"date": "2026-11-10", "index_value": 80},
        ]
    )
    scored = _by_date(build_score_rows(rows, {}, 1))
    assert scored["2026-11-01"]["color"] == "calibrating"
    assert scored["2026-11-01"]["day_type"] == "weekend"
    assert scored["2026-11-07"]["color"] == "red"
    assert scored["2026-11-07"]["day_type"] == "weekend"
    assert scored["2026-11-10"]["color"] == "yellow"
    assert scored["2026-11-10"]["day_type"] == "weekday"
    assert "waiting for another weekend" not in scored["2026-11-07"]["reason"]


def test_news_weight_and_missing_news_flow():
    frame = _frame(
        [
            {"date": "2026-11-02", "index_value": 100},
            {"date": "2026-11-03", "index_value": 100},
            {"date": "2026-11-04", "index_value": 40},
        ]
    )
    news = {
        "2026-11-02": {"impressions": 100, "top_story_impressions": 100},
        "2026-11-03": {"impressions": 10, "top_story_impressions": 10},
    }
    blended = _by_date(build_score_rows(frame, news, 0.7))
    assert blended["2026-11-03"]["news_flow"] == 0
    assert blended["2026-11-03"]["score"] == 70
    assert blended["2026-11-03"]["attention_weight"] == 0.7
    assert blended["2026-11-03"]["news_weight"] == 0.3
    assert blended["2026-11-04"]["score"] == 40
    assert blended["2026-11-04"]["news_weight"] == 0
    assert "no story impressions stored" in blended["2026-11-04"]["reason"]

    attention_only = _by_date(build_score_rows(frame, news, 1))
    assert attention_only["2026-11-03"]["score"] == 100
    assert attention_only["2026-11-03"]["news_weight"] == 0


def test_reason_uses_price_volume_and_story_impressions():
    frame = _frame(
        [
            {"date": "2026-11-02", "index_value": 40, "copper_close": 5.0, "pct_post_count": 40},
            {"date": "2026-11-03", "index_value": 80, "copper_close": 5.105, "pct_post_count": 85},
        ]
    )
    news = {
        "2026-11-02": {"impressions": 100, "top_story_impressions": 100},
        "2026-11-03": {"impressions": 300, "top_story_impressions": 300},
    }
    reason = _by_date(build_score_rows(frame, news, 0.7))["2026-11-03"]["reason"]
    assert reason.startswith("Calibrating: ")
    assert "copper up 2.1%" in reason
    assert "X volume in the 85th percentile" in reason
    assert "one major story at 3x normal impressions" in reason


def test_scores_round_trip_through_sqlite(tmp_path):
    from copper_today.db import connect, list_scores, save_scores

    rows = build_score_rows(
        _frame([{"date": "2026-11-02", "index_value": 12.5, "copper_close": 4.0}]),
        {},
        0.7,
    )
    with connect(tmp_path / "copper.db") as conn:
        save_scores(conn, rows)
        stored = list_scores(conn)
    assert stored[0]["date"] == "2026-11-02"
    assert stored[0]["score"] == 12.5
    assert stored[0]["color"] == "calibrating"
    assert json.dumps(stored[0]["reason"])
