import json

import pytest

from copper_today.config import STORY_MODEL
from copper_today.stories import (
    SYSTEM_PROMPT,
    StoryError,
    assemble_stories,
    build_user_prompt,
    estimate_cost,
    group_stored_posts,
    normalize_posts,
    parse_groups,
)


def _post(post_id: str, text: str, impressions: int, engagement: int) -> dict:
    return {
        "post_id": post_id,
        "text": text,
        "impression_count": impressions,
        "engagement": engagement,
    }


def test_normalize_keeps_metric_fields_and_drops_rows_without_ids():
    posts = normalize_posts(
        [
            {
                "id": "101",
                "text": "  Reuters   says copper inventories fell  ",
                "public_metrics": {
                    "impression_count": 12,
                    "like_count": 2,
                    "retweet_count": 1,
                    "reply_count": 3,
                    "quote_count": 4,
                },
            },
            {"text": "no id"},
            {"id": "abc", "text": "not a status id"},
        ]
    )
    assert len(posts) == 1
    assert posts[0]["post_id"] == "101"
    assert posts[0]["text"] == "Reuters says copper inventories fell"
    assert posts[0]["engagement"] == 10
    assert posts[0]["impression_count"] == 12


def test_prompt_sends_text_only_and_forbids_outside_facts():
    prompt = build_user_prompt([_post("101", "Copper inventories fell.", 99, 4)])
    assert "id: 101" in prompt
    assert "Copper inventories fell." in prompt
    assert "impression_count" not in prompt
    assert "99" not in prompt
    assert "Never add facts" in SYSTEM_PROMPT
    assert "copper price and macro" in SYSTEM_PROMPT
    assert "junior explorers" in SYSTEM_PROMPT
    assert "policy and supply chain" in SYSTEM_PROMPT


def test_ranking_uses_stored_metrics_and_drops_invented_ids():
    posts = [
        _post("1", "A junior explorer mentioned a drill result.", 10, 1),
        _post("2", "A mine update from the company.", 100, 5),
    ]
    groups = [
        {
            "headline": "Explorer chatter",
            "summary": "A post mentions a junior explorer drill result.",
            "category": "junior explorers",
            "unverified": True,
            "post_ids": ["1", "999"],
        },
        {
            "headline": "Mine update",
            "summary": "A post says there was a mine update from the company.",
            "category": "Mining and Project News",
            "unverified": False,
            "post_ids": ["2", "1"],
        },
    ]
    stories = assemble_stories(posts, parse_groups(json.dumps({"stories": groups})))
    assert [story["headline"] for story in stories] == ["Mine update", "Explorer chatter"]
    assert stories[0]["impressions"] == 100
    assert stories[0]["engagement"] == 5
    assert stories[0]["unverified"] is False
    assert stories[0]["category"] == "mining and project news"
    assert [link["id"] for link in stories[0]["links"]] == ["2"]
    assert stories[0]["links"][0]["url"] == "https://x.com/i/web/status/2"
    assert stories[1]["unverified"] is True
    assert all(link["id"] != "999" for story in stories for link in story["links"])


def test_cost_uses_the_short_and_long_haiku_rates():
    assert estimate_cost(1_000, 100) == pytest.approx(0.00015)
    long = estimate_cost(100_001, 10)
    assert long == pytest.approx((100_001 * 0.50 + 10 * 2.50) / 1_000_000)


def test_one_claude_call_groups_posts_and_hides_the_key():
    payload = {
        "stories": [
            {
                "headline": "Inventories",
                "summary": "Posts say copper inventories fell.",
                "category": "policy and supply chain",
                "unverified": True,
                "post_ids": ["101"],
            }
        ]
    }
    calls = []

    def post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})

        class Response:
            status_code = 200

            def json(self):
                return {
                    "model": STORY_MODEL,
                    "content": [{"type": "text", "text": "```json\n" + json_dumps(payload) + "\n```"}],
                    "usage": {"input_tokens": 1000, "output_tokens": 100},
                }

        return Response()

    grouped = group_stored_posts(
        [_post("101", "Posts say copper inventories fell.", 12, 3)],
        api_key="secret-key",
        post=post,
    )
    assert len(calls) == 1
    body = calls[0]["json"]
    assert body["model"] == STORY_MODEL
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"] == {"effort": "low"}
    assert calls[0]["headers"]["x-api-key"] == "secret-key"
    assert grouped.stories[0]["impressions"] == 12
    assert grouped.estimated_cost_usd == pytest.approx(0.00015)
    assert grouped.input_tokens == 1000

    def rejected(url, headers, json, timeout):
        class Response:
            status_code = 401

            def json(self):
                return {"error": {"message": "bad key secret-key"}}

        return Response()

    with pytest.raises(StoryError) as caught:
        group_stored_posts([_post("101", "Copper inventories fell.", 1, 0)], api_key="secret-key", post=rejected)
    assert "secret-key" not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def json_dumps(payload: dict) -> str:
    return json.dumps(payload)
