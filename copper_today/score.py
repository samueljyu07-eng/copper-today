"""Green / yellow / red score from the attention index and top-story impressions."""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

import pandas as pd

from copper_today.analytics import percentile_rank
from copper_today.config import (
    SCORE_GREEN_PERCENTILE,
    SCORE_MIN_DAYS,
    SCORE_RED_PERCENTILE,
    SCORE_WINDOW_DAYS,
)
from copper_today.dates import to_iso_date


def build_score_rows(
    frame: pd.DataFrame,
    news_by_date: dict[str, dict],
    attention_weight: float,
) -> list[dict]:
    """Score each day that has an attention index.

    Weekends are ranked only against weekends, and weekdays only against weekdays.
    Colour needs 7 days with an index. Before that the badge stays calibrating.
    A missing news reading drops out of the average instead of counting as zero.
    """
    weight = round(min(1.0, max(0.0, float(attention_weight))), 4)
    days = _days(frame)
    if not days:
        return []

    drafted = []
    previous_close = None
    history = 0
    for day in days:
        if day["index"] is None:
            if day["close"] is not None:
                previous_close = day["close"]
            continue
        history += 1
        news = news_by_date.get(day["iso"]) or {}
        news_impressions = news.get("impressions")
        top_story = news.get("top_story_impressions")
        change = None
        if day["close"] is not None and previous_close not in (None, 0):
            change = (day["close"] - previous_close) / previous_close * 100.0
        if day["close"] is not None:
            previous_close = day["close"]
        news_flow = _news_flow(day, days, news_by_date)
        if news_flow is None:
            score = day["index"]
            used_attention = 1.0
            used_news = 0.0
        else:
            used_attention = weight
            used_news = round(1.0 - weight, 4)
            score = used_attention * day["index"] + used_news * news_flow
        drafted.append(
            {
                "date": day["iso"],
                "day": day["day"],
                "weekend": day["weekend"],
                "attention_index": day["index"],
                "news_impressions": None if news_impressions is None else int(news_impressions),
                "news_flow": news_flow,
                "score": float(score),
                "attention_weight": used_attention,
                "news_weight": used_news,
                "price_change_pct": change,
                "post_count_percentile": day["post_percentile"],
                "top_story_impressions": None if top_story is None else int(top_story),
                "history_days": history,
            }
        )

    stamped = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for item in drafted:
        color, score_percentile, comparison_days = _color(item, drafted)
        reason = _reason(item, drafted, color)
        rows.append(
            {
                "date": item["date"],
                "attention_index": item["attention_index"],
                "news_impressions": item["news_impressions"],
                "news_flow": item["news_flow"],
                "score": item["score"],
                "color": color,
                "reason": reason,
                "attention_weight": item["attention_weight"],
                "news_weight": item["news_weight"],
                "price_change_pct": item["price_change_pct"],
                "post_count_percentile": item["post_count_percentile"],
                "top_story_impressions": item["top_story_impressions"],
                "score_percentile": score_percentile,
                "day_type": "weekend" if item["weekend"] else "weekday",
                "history_days": item["history_days"],
                "comparison_days": comparison_days,
                "computed_at": stamped,
            }
        )
    return rows


def _days(frame: pd.DataFrame) -> list[dict]:
    if frame is None or frame.empty or "date" not in frame.columns:
        return []
    rows = []
    for record in frame.to_dict(orient="records"):
        iso = to_iso_date(record.get("date"))
        if iso is None:
            continue
        day = datetime.strptime(iso, "%Y-%m-%d").date()
        rows.append(
            {
                "iso": iso,
                "day": day,
                "weekend": day.weekday() >= 5,
                "index": _finite(record.get("index_value")),
                "close": _finite(record.get("copper_close")),
                "post_percentile": _finite(record.get("pct_post_count")),
            }
        )
    deduped = {row["iso"]: row for row in rows}
    return [deduped[iso] for iso in sorted(deduped)]


def _news_flow(day: dict, days: list[dict], news_by_date: dict[str, dict]) -> float | None:
    own = news_by_date.get(day["iso"]) or {}
    if own.get("impressions") is None:
        return None
    start = day["day"] - timedelta(days=SCORE_WINDOW_DAYS - 1)
    sample = []
    for other in days:
        if other["index"] is None or other["weekend"] != day["weekend"]:
            continue
        if not (start <= other["day"] <= day["day"]):
            continue
        impressions = (news_by_date.get(other["iso"]) or {}).get("impressions")
        if impressions is None:
            continue
        sample.append(float(impressions))
    if len(sample) < 2:
        return None
    return percentile_rank(sample, float(own["impressions"]))


def _color(item: dict, drafted: list[dict]) -> tuple[str, float | None, int]:
    start = item["day"] - timedelta(days=SCORE_WINDOW_DAYS - 1)
    window = [
        other
        for other in drafted
        if other["weekend"] == item["weekend"] and start <= other["day"] <= item["day"]
    ]
    if item["history_days"] < SCORE_MIN_DAYS or len(window) < 2:
        return "calibrating", None, len(window)
    rank = percentile_rank([other["score"] for other in window], item["score"])
    if rank >= SCORE_GREEN_PERCENTILE:
        color = "green"
    elif rank <= SCORE_RED_PERCENTILE:
        color = "red"
    else:
        color = "yellow"
    return color, rank, len(window)


def _reason(item: dict, drafted: list[dict], color: str) -> str:
    label = {"green": "Green", "yellow": "Yellow", "red": "Red", "calibrating": "Calibrating"}[color]
    parts = []
    change = item["price_change_pct"]
    if change is not None:
        if abs(change) < 0.05:
            parts.append("copper unchanged")
        else:
            direction = "up" if change > 0 else "down"
            parts.append(f"copper {direction} {abs(change):.1f}%")
    if item["post_count_percentile"] is not None:
        parts.append(f"X volume in the {_ordinal(item['post_count_percentile'])} percentile")
    multiple = _story_multiple(item, drafted)
    if multiple is not None:
        parts.append(f"one major story at {_multiple_text(multiple)} normal impressions")
    elif item["top_story_impressions"]:
        parts.append(f"top story at {item['top_story_impressions']:,} impressions")
    elif item["news_impressions"] is None:
        parts.append("no story impressions stored")
    if color == "calibrating":
        if item["history_days"] < SCORE_MIN_DAYS:
            parts.append(f"{item['history_days']} of {SCORE_MIN_DAYS} days stored")
        else:
            kind = "weekend" if item["weekend"] else "weekday"
            parts.append(f"waiting for another {kind} in the last {SCORE_WINDOW_DAYS} days")
    if not parts:
        parts.append("not enough component readings")
    return f"{label}: " + ", ".join(parts)


def _story_multiple(item: dict, drafted: list[dict]) -> float | None:
    top = item["top_story_impressions"]
    if not top:
        return None
    start = item["day"] - timedelta(days=SCORE_WINDOW_DAYS - 1)
    baseline = [
        other["top_story_impressions"]
        for other in drafted
        if other["date"] != item["date"]
        and other["weekend"] == item["weekend"]
        and start <= other["day"] < item["day"]
        and other["top_story_impressions"]
    ]
    if not baseline:
        return None
    normal = float(statistics.median(baseline))
    if normal <= 0:
        return None
    ratio = top / normal
    if ratio < 1.5:
        return None
    return ratio


def _multiple_text(ratio: float) -> str:
    if abs(ratio - round(ratio)) < 0.05:
        return f"{int(round(ratio))}x"
    return f"{ratio:.1f}x"


def _ordinal(value: float) -> str:
    number = int(round(value))
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
