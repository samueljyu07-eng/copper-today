from datetime import date, timedelta

import pandas as pd
import pytest

from copper_today.analytics import percentile_rank, prepare


def test_percentile_rank_ends_and_ties():
    assert percentile_rank([1, 2, 3, 4, 5], 5) == 100
    assert percentile_rank([1, 2, 3, 4, 5], 1) == 0
    assert percentile_rank([1, 2, 3, 4, 5], 3) == 50
    assert percentile_rank([7, 7, 7, 7], 7) == 50


def test_percentile_window_ignores_older_outlier():
    start = date(2026, 1, 1)
    rows = [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "post_count": 1000 if offset == 0 else offset + 1,
        }
        for offset in range(40)
    ]
    frame = prepare(rows).frame
    assert frame.iloc[-1]["pct_post_count"] == pytest.approx(100)
    assert pd.isna(frame.iloc[8]["pct_post_count"])
    assert pd.notna(frame.iloc[9]["pct_post_count"])


def test_index_averages_only_available_components():
    start = date(2026, 10, 1)
    rows = [
        {
            "date": (start + timedelta(days=offset)).isoformat(),
            "post_count": offset + 1,
            "copper_close": 100 - offset,
        }
        for offset in range(15)
    ]
    last = prepare(rows).frame.iloc[-1]
    assert last["pct_post_count"] == pytest.approx(100)
    assert last["pct_copper_close"] == pytest.approx(0)
    assert last["index_value"] == pytest.approx(50)
    assert last["index_components"] == 2


def test_flat_series_stays_near_fifty_and_unflagged():
    start = date(2026, 10, 1)
    rows = [
        {"date": (start + timedelta(days=offset)).isoformat(), "copper_close": 5}
        for offset in range(25)
    ]
    result = prepare(rows)
    indexed = result.frame.dropna(subset=["index_value"])
    assert len(indexed) >= 10
    assert indexed["index_value"].eq(50).all()
    assert result.index_p75 == pytest.approx(50)
    assert not result.frame["flagged"].any()


def test_spike_is_elevated_and_rising():
    start = date(2026, 10, 1)
    rows = []
    for offset in range(21):
        value = 10 if offset < 20 else 100
        rows.append(
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "post_count": value,
                "impression_sum": value,
                "impression_median": value,
                "trends_interest": value,
                "copper_close": value,
            }
        )
    frame = prepare(rows).frame
    assert frame.iloc[-1]["elevated"]
    assert frame.iloc[-1]["rising"]
    assert "Above 75th percentile" in frame.iloc[-1]["flag"]
    assert "Rising" in frame.iloc[-1]["flag"]
    assert not frame.iloc[-2]["elevated"]
    assert not frame.iloc[-2]["rising"]


def test_rising_uses_the_previous_index_reading_across_a_gap():
    start = date(2026, 10, 1)
    rows = [
        {"date": (start + timedelta(days=offset)).isoformat(), "post_count": 10}
        for offset in range(12)
    ]
    rows.append({"date": (start + timedelta(days=12)).isoformat(), "post_count": None})
    rows.append({"date": (start + timedelta(days=13)).isoformat(), "post_count": 50})
    frame = prepare(rows).frame
    assert bool(frame.iloc[-2]["rising"]) is False
    assert bool(frame.iloc[-1]["rising"]) is True
    assert frame.iloc[-1]["index_value"] == pytest.approx(100)


def test_moving_average_needs_seven_stored_values():
    start = date(2026, 10, 1)
    values = [1, 1, 1, None, 1, 1, 1, 1]
    rows = [
        {"date": (start + timedelta(days=offset)).isoformat(), "post_count": value}
        for offset, value in enumerate(values)
    ]
    averages = prepare(rows).frame["post_count_ma_7"]
    assert averages.iloc[:-1].isna().all()
    assert averages.iloc[-1] == pytest.approx(1)


def test_prepare_empty():
    result = prepare([])
    assert result.frame.empty
    assert result.index_p75 is None
