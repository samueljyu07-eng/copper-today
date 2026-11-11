"""X recent counts and one page of recent search.

Billing guard: each snapshot makes at most two HTTP calls, and the search call
is never paginated. Do not add a next_token loop.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone

import requests

from copper_today.config import RECENT_SEARCH_DAYS, X_API_BASE, X_MAX_RESULTS


class SourceError(Exception):
    pass


def in_recent_window(day: date, today: date) -> bool:
    earliest = today - timedelta(days=RECENT_SEARCH_DAYS - 1)
    return earliest <= day <= today


def day_bounds(day: date, now: datetime) -> tuple[str, str]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    latest = now.astimezone(timezone.utc) - timedelta(seconds=30)
    if end > latest:
        end = latest
    if end <= start:
        raise SourceError(
            "That UTC day has not been open long enough to query. Wait a minute and rerun."
        )
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(stamp), end.strftime(stamp)


def parse_recent_count(payload: dict) -> int:
    meta = payload.get("meta") or {}
    if "total_tweet_count" in meta:
        return int(meta["total_tweet_count"])
    data = payload.get("data") or []
    if data:
        return sum(int(bucket.get("tweet_count") or 0) for bucket in data)
    raise SourceError("X counts response did not include a total.")


def summarize_impressions(posts: list[dict]) -> dict:
    if not posts:
        return {
            "posts_sampled": 0,
            "impression_sum": 0,
            "impression_median": None,
            "impressions_missing": 0,
        }
    counts: list[int] = []
    missing = 0
    for post in posts:
        metrics = post.get("public_metrics") or {}
        value = metrics.get("impression_count")
        if value is None:
            missing += 1
            continue
        counts.append(int(value))
    return {
        "posts_sampled": len(posts),
        "impression_sum": int(sum(counts)) if counts else None,
        "impression_median": float(statistics.median(counts)) if counts else None,
        "impressions_missing": missing,
    }


def _error_detail(payload: dict, text: str) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("title") or ""
        errors = payload.get("errors") or []
        if not detail and errors:
            messages = []
            for item in errors[:3]:
                if isinstance(item, dict):
                    messages.append(str(item.get("message") or item.get("title") or item))
                else:
                    messages.append(str(item))
            detail = "; ".join(messages)
        if detail:
            return " ".join(str(detail).split())[:400]
    return " ".join((text or "").split())[:400]


def _parse_response(response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    status = getattr(response, "status_code", 0)
    if status == 200:
        return payload
    detail = _error_detail(payload, getattr(response, "text", "") or "")
    if status == 401:
        raise SourceError("X rejected the bearer token. Check X_BEARER_TOKEN in .env.")
    if status in (402, 403):
        message = (
            "X refused the request. Check credits and the monthly spending limit "
            "in the X Developer Console."
        )
        if detail:
            message = f"{message} {detail}"
        raise SourceError(message)
    if status == 429:
        raise SourceError("X rate limit reached. Wait and run the job again.")
    suffix = f" {detail}" if detail else ""
    raise SourceError(f"X request failed ({status}).{suffix}")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "copper-attention-dashboard",
    }


def fetch_recent_count(
    token: str,
    query: str,
    day: date,
    now: datetime,
    *,
    get=requests.get,
) -> int:
    start, end = day_bounds(day, now)
    # One attempt on purpose. A retry can bill a second counts request if the
    # first one was accepted and the response never arrived.
    response = get(
        f"{X_API_BASE}/tweets/counts/recent",
        headers=_headers(token),
        params={
            "query": query,
            "granularity": "day",
            "start_time": start,
            "end_time": end,
        },
        timeout=30,
    )
    return parse_recent_count(_parse_response(response))


def fetch_top_posts(
    token: str,
    query: str,
    day: date,
    now: datetime,
    *,
    get=requests.get,
) -> list[dict]:
    start, end = day_bounds(day, now)
    response = _search(get, token, query, start, end, "relevancy")
    if (
        getattr(response, "status_code", 0) == 400
        and "sort" in (getattr(response, "text", "") or "").lower()
    ):
        response = _search(get, token, query, start, end, "recency")
    payload = _parse_response(response)
    data = payload.get("data") or []
    if not isinstance(data, list):
        raise SourceError("X search response did not include a post list.")
    return data


def _search(get, token: str, query: str, start: str, end: str, sort_order: str):
    # max_results is the whole page. meta.next_token is intentionally ignored.
    return get(
        f"{X_API_BASE}/tweets/search/recent",
        headers=_headers(token),
        params={
            "query": query,
            "max_results": X_MAX_RESULTS,
            "sort_order": sort_order,
            "tweet.fields": "public_metrics",
            "start_time": start,
            "end_time": end,
        },
        timeout=30,
    )
