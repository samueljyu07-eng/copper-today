"""Google Trends interest for the fixed keyword basket."""

from __future__ import annotations

import time
from collections.abc import Sequence

import pandas as pd

from copper_today.config import TRENDS_GEO, TRENDS_KEYWORDS, TRENDS_TIMEFRAME
from copper_today.dates import to_iso_date

PARTIAL_COLUMNS = {"ispartial", "is_partial"}


def basket_from_frame(frame, keywords: Sequence[str]) -> dict[str, float]:
    if frame is None:
        return {}
    if isinstance(frame, list):
        frame = pd.DataFrame(frame)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    data = frame.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    if "date" in data.columns:
        data = data.set_index("date")
    elif "time" in data.columns:
        data = data.set_index("time")

    partial_name = next((column for column in data.columns if str(column).lower() in PARTIAL_COLUMNS), None)
    if partial_name is not None:
        partial = data[partial_name].map(lambda value: bool(value) if pd.notna(value) else False)
        data = data.loc[~partial].drop(columns=[partial_name])

    columns = []
    for keyword in keywords:
        if keyword in data.columns:
            columns.append(keyword)
            continue
        match = next((column for column in data.columns if str(column).lower() == keyword.lower()), None)
        if match is not None:
            columns.append(match)
    if not columns:
        return {}

    means = data[columns].apply(pd.to_numeric, errors="coerce").mean(axis=1)
    buckets: dict[str, list[float]] = {}
    for stamp, value in means.items():
        if pd.isna(value):
            continue
        day = to_iso_date(stamp)
        if day is None:
            continue
        buckets.setdefault(day, []).append(float(value))
    return {day: round(sum(values) / len(values), 2) for day, values in buckets.items()}


def fetch_trends_basket(
    keywords: Sequence[str] | None = None,
    *,
    timeframe: str = TRENDS_TIMEFRAME,
    geo: str = TRENDS_GEO,
) -> tuple[dict[str, float], str | None]:
    keywords = list(keywords or TRENDS_KEYWORDS)
    last_error = "Google Trends request failed."
    for attempt in range(2):
        series, error = _fetch_trends_once(keywords, timeframe=timeframe, geo=geo)
        if error is None:
            return series, None
        last_error = error
        if "429" not in error or attempt == 1:
            break
        time.sleep(15)
    return {}, last_error


def _fetch_trends_once(
    keywords: list[str],
    *,
    timeframe: str,
    geo: str,
) -> tuple[dict[str, float], str | None]:
    try:
        from trendspy import Trends
    except ImportError:
        return {}, "trendspy is not installed."
    try:
        try:
            client = Trends(request_delay=1.0, tzs=0)
        except TypeError:
            client = Trends()
        frame = client.interest_over_time(keywords, timeframe=timeframe, geo=geo)
    except Exception as exc:
        return {}, f"Google Trends request failed: {' '.join(str(exc).split())[:400]}"
    series = basket_from_frame(frame, keywords)
    if not series:
        return {}, "Google Trends returned no interest data for the keyword basket."
    return series, None
