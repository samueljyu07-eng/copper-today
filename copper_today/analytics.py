"""30-day percentile ranks, the attention index, and flags."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from copper_today.config import (
    ELEVATED_PERCENTILE,
    METRICS,
    MIN_PERCENTILE_OBS,
    MOVING_AVERAGE_POINTS,
    PERCENTILE_WINDOW_DAYS,
)


@dataclass
class Analytics:
    frame: pd.DataFrame
    index_p75: float | None


def percentile_rank(sample: list[float], value: float) -> float:
    """Scale the average rank of `value` onto 0–100. The sample minimum is 0 and the maximum is 100.

    Ties sit in the middle of their rank span, so a flat sample is 50 rather than 100.
    """
    if len(sample) < 2:
        raise ValueError("percentile_rank needs at least two values")
    below = sum(point < value for point in sample)
    equal = sum(point == value for point in sample)
    if equal == 0:
        raise ValueError("percentile_rank expects the value to be inside the sample")
    rank = below + (equal + 1) / 2.0
    return 100.0 * (rank - 1) / (len(sample) - 1)


def percentile_level(values: list[float], quantile: float) -> float:
    array = np.asarray(values, dtype=float)
    array = array[~np.isnan(array)]
    try:
        return float(np.percentile(array, quantile, method="linear"))
    except TypeError:
        return float(np.percentile(array, quantile, interpolation="linear"))


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.Timestamp(value)
    return parsed.date()


def _missing(value) -> bool:
    return value is None or pd.isna(value)


def _window_percentiles(dates: list[date], values: list) -> list[float]:
    out: list[float] = []
    for index, (day, value) in enumerate(zip(dates, values)):
        if _missing(value):
            out.append(np.nan)
            continue
        start = day - timedelta(days=PERCENTILE_WINDOW_DAYS - 1)
        sample = [
            float(point)
            for point_day, point in zip(dates[: index + 1], values[: index + 1])
            if point_day >= start and not _missing(point)
        ]
        if len(sample) < MIN_PERCENTILE_OBS:
            out.append(np.nan)
            continue
        out.append(percentile_rank(sample, float(value)))
    return out


def _trailing_mean(values: list, window: int = MOVING_AVERAGE_POINTS) -> list[float]:
    out: list[float] = []
    seen: list[float] = []
    for value in values:
        if _missing(value):
            out.append(np.nan)
            continue
        seen.append(float(value))
        if len(seen) < window:
            out.append(np.nan)
        else:
            out.append(sum(seen[-window:]) / window)
    return out


def _flag_label(elevated: bool, rising: bool) -> str:
    parts = []
    if elevated:
        parts.append("Above 75th percentile")
    if rising:
        parts.append("Rising")
    return " · ".join(parts)


def prepare(rows: list[dict]) -> Analytics:
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=["date", *METRICS])
    if "date" not in frame.columns:
        frame["date"] = pd.NaT
    for metric in METRICS:
        if metric not in frame.columns:
            frame[metric] = np.nan
        frame[metric] = pd.to_numeric(frame[metric], errors="coerce")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    frame = frame.drop_duplicates("date", keep="last").reset_index(drop=True)

    dates = [_as_date(value) for value in frame["date"]]
    for metric in METRICS:
        frame[f"pct_{metric}"] = _window_percentiles(dates, frame[metric].tolist())

    pct_cols = [f"pct_{metric}" for metric in METRICS]
    frame["index_components"] = frame[pct_cols].notna().sum(axis=1).astype(int)
    frame["index_value"] = frame[pct_cols].mean(axis=1, skipna=True)
    frame.loc[frame["index_components"] == 0, "index_value"] = np.nan
    frame["index_ma_7"] = _trailing_mean(frame["index_value"].tolist())
    for metric in METRICS:
        frame[f"{metric}_ma_7"] = _trailing_mean(frame[metric].tolist())

    indexed = frame["index_value"].dropna()
    p75 = (
        percentile_level(indexed.tolist(), ELEVATED_PERCENTILE)
        if len(indexed) >= MIN_PERCENTILE_OBS
        else None
    )
    if p75 is None:
        frame["elevated"] = False
    else:
        frame["elevated"] = frame["index_value"].gt(p75) & frame["index_value"].notna()

    frame["index_previous"] = frame["index_value"].ffill().shift(1)
    frame["rising"] = (
        frame["index_value"].notna()
        & frame["index_previous"].notna()
        & frame["index_value"].gt(frame["index_previous"])
    )
    frame["flagged"] = frame["elevated"] | frame["rising"]
    frame["flag"] = [
        _flag_label(bool(elevated), bool(rising))
        for elevated, rising in zip(frame["elevated"].tolist(), frame["rising"].tolist())
    ]
    return Analytics(frame=frame, index_p75=p75)
