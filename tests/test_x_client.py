from datetime import date, datetime, timezone

import pytest

from copper_today.config import COPPER_QUERY, TRENDS_KEYWORDS
from copper_today.x_client import (
    SourceError,
    day_bounds,
    fetch_recent_count,
    fetch_top_posts,
    in_recent_window,
    parse_recent_count,
    summarize_impressions,
)

NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = {} if payload is None else payload
        self.text = text

    def json(self):
        return self._payload


def test_query_fits_the_recent_search_limit():
    assert len(COPPER_QUERY) <= 512
    assert "-is:retweet" in COPPER_QUERY
    assert len(TRENDS_KEYWORDS) <= 5


def test_recent_window_is_seven_days():
    today = date(2026, 11, 11)
    assert in_recent_window(date(2026, 11, 11), today)
    assert in_recent_window(date(2026, 11, 5), today)
    assert not in_recent_window(date(2026, 11, 4), today)


def test_day_bounds_cover_a_finished_day_and_clamp_today():
    start, end = day_bounds(date(2026, 11, 10), NOW)
    assert start == "2026-11-10T00:00:00Z"
    assert end == "2026-11-11T00:00:00Z"
    start, end = day_bounds(date(2026, 11, 11), NOW)
    assert start == "2026-11-11T00:00:00Z"
    assert end == "2026-11-11T17:59:30Z"


def test_day_bounds_rejects_a_day_that_just_opened():
    now = datetime(2026, 11, 11, 0, 0, 10, tzinfo=timezone.utc)
    with pytest.raises(SourceError):
        day_bounds(date(2026, 11, 11), now)


def test_parse_recent_count_prefers_the_meta_total():
    assert parse_recent_count({"meta": {"total_tweet_count": 4}, "data": [{"tweet_count": 9}]}) == 4
    with pytest.raises(SourceError):
        parse_recent_count({})


def test_summarize_impressions():
    summary = summarize_impressions(
        [
            {"public_metrics": {"impression_count": 10}},
            {"public_metrics": {}},
            {"public_metrics": {"impression_count": 30}},
        ]
    )
    assert summary["posts_sampled"] == 3
    assert summary["impression_sum"] == 40
    assert summary["impression_median"] == 20
    assert summary["impressions_missing"] == 1
    empty = summarize_impressions([])
    assert empty["impression_sum"] == 0
    assert empty["impression_median"] is None
    assert empty["posts_sampled"] == 0


def test_counts_request_is_a_single_call():
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append((url, params, headers))
        if len(calls) > 1:
            raise AssertionError("counts request was repeated")
        return Response(
            200,
            {
                "meta": {"total_tweet_count": 12, "next_token": "DO_NOT_FOLLOW"},
                "data": [{"tweet_count": 99}],
            },
        )

    total = fetch_recent_count("secret-token", COPPER_QUERY, date(2026, 11, 10), NOW, get=get)
    assert total == 12
    url, params, headers = calls[0]
    assert url.endswith("/tweets/counts/recent")
    assert params["granularity"] == "day"
    assert params["query"] == COPPER_QUERY
    assert params["start_time"].startswith("2026-11-10")
    assert "next_token" not in params
    assert headers["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in url


def test_search_stops_at_one_hundred_posts():
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        if len(calls) > 1:
            raise AssertionError("search followed next_token")
        return Response(
            200,
            {
                "data": [{"id": "1", "public_metrics": {"impression_count": 10}}],
                "meta": {"result_count": 1, "next_token": "DO_NOT_FOLLOW"},
            },
        )

    posts = fetch_top_posts("secret-token", COPPER_QUERY, date(2026, 11, 10), NOW, get=get)
    assert len(posts) == 1
    assert calls[0]["max_results"] == 100
    assert calls[0]["sort_order"] == "relevancy"
    assert calls[0]["tweet.fields"] == "public_metrics"
    assert "expansions" not in calls[0]
    assert "user.fields" not in calls[0]
    assert "next_token" not in calls[0]


def test_relevancy_sort_falls_back_once():
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params["sort_order"])
        if len(calls) == 1:
            return Response(400, {"detail": "sort_order is not available"}, text="sort_order is not available")
        return Response(
            200,
            {"data": [{"public_metrics": {"impression_count": 4}}], "meta": {"result_count": 1}},
        )

    posts = fetch_top_posts("token", "copper", date(2026, 11, 10), NOW, get=get)
    assert calls == ["relevancy", "recency"]
    assert posts[0]["public_metrics"]["impression_count"] == 4


def test_unrelated_search_error_is_not_retried():
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params["sort_order"])
        return Response(400, {"detail": "query is invalid"}, text="query is invalid")

    with pytest.raises(SourceError):
        fetch_top_posts("token", "copper", date(2026, 11, 10), NOW, get=get)
    assert calls == ["relevancy"]


def test_unauthorized_does_not_echo_the_token():
    def get(url, headers=None, params=None, timeout=None):
        return Response(401, {"title": "Unauthorized"}, text="Unauthorized")

    with pytest.raises(SourceError) as caught:
        fetch_recent_count("super-secret-token", "copper", date(2026, 11, 10), NOW, get=get)
    message = str(caught.value)
    assert "super-secret-token" not in message
    assert "bearer" in message.lower()
