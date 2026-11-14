"""Group one day's stored posts into stories. One Claude call, no extra X reads."""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests

from copper_today.config import (
    STORY_CATEGORIES,
    STORY_INPUT_USD_PER_MTOK,
    STORY_LONG_CONTEXT_TOKENS,
    STORY_LONG_INPUT_USD_PER_MTOK,
    STORY_LONG_OUTPUT_USD_PER_MTOK,
    STORY_MODEL,
    STORY_OUTPUT_USD_PER_MTOK,
)

SYSTEM_PROMPT = """You group posts from X into distinct copper stories. Use only the post text in the user message. Never add facts, numbers, names, places, or claims that are not written in those posts. Do not use knowledge from outside the posts.

Return JSON only, with no markdown fence, in this shape:
{"stories":[{"headline":"","summary":"","category":"","unverified":true,"post_ids":["id"]}]}

Rules:
- headline is a short line.
- summary is one or two neutral sentences.
- category is exactly one of: copper price and macro; mining and project news; junior explorers; policy and supply chain.
- Consider all four categories, and create a story only when the posts support it.
- Group posts that describe the same event. A post id may appear in only one story.
- unverified is false only when the posts name a source, company, outlet, or account behind the claim. Otherwise unverified is true.
- If the posts do not support a story, return {"stories":[]}."""


class StoryError(Exception):
    pass


@dataclass
class GroupedStories:
    stories: list[dict]
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


def normalize_posts(posts: list[dict] | None) -> list[dict]:
    """Keep the fields the story call needs from the search page already fetched."""
    normalized = []
    seen: set[str] = set()
    for post in posts or []:
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id") or "").strip()
        text = " ".join(str(post.get("text") or "").split())
        if not post_id.isdigit() or not text or post_id in seen:
            continue
        seen.add(post_id)
        metrics = post.get("public_metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        impressions = _metric_int(metrics, "impression_count")
        likes = _metric_int(metrics, "like_count", 0)
        reposts = _metric_int(metrics, "retweet_count", 0)
        replies = _metric_int(metrics, "reply_count", 0)
        quotes = _metric_int(metrics, "quote_count", 0)
        normalized.append(
            {
                "post_id": post_id,
                "text": text[:1000],
                "impression_count": impressions,
                "like_count": likes,
                "repost_count": reposts,
                "reply_count": replies,
                "quote_count": quotes,
                "engagement": likes + reposts + replies + quotes,
            }
        )
    return normalized


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Haiku 5.5 published rates. Prompts over 100k tokens use the long-context rate."""
    if input_tokens > STORY_LONG_CONTEXT_TOKENS:
        input_rate = STORY_LONG_INPUT_USD_PER_MTOK
        output_rate = STORY_LONG_OUTPUT_USD_PER_MTOK
    else:
        input_rate = STORY_INPUT_USD_PER_MTOK
        output_rate = STORY_OUTPUT_USD_PER_MTOK
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def format_cost(cost: float) -> str:
    if cost >= 0.01:
        return f"${cost:.2f}"
    if cost >= 0.0001:
        return f"${cost:.4f}"
    return f"${cost:.6f}"


def build_user_prompt(posts: list[dict]) -> str:
    blocks = []
    for post in posts[:100]:
        text = " ".join(str(post.get("text") or "").split())[:500]
        blocks.append(f"id: {post['post_id']}\n{text}")
    return "Posts:\n\n" + "\n\n".join(blocks)


def canonical_category(value) -> str:
    cleaned = " ".join(str(value or "").lower().split())
    for category in STORY_CATEGORIES:
        if cleaned == category:
            return category
    return ""


def parse_groups(text: str) -> list[dict]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise StoryError("Claude did not return JSON.")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise StoryError("Claude returned JSON that could not be read.") from exc
    stories = payload.get("stories") if isinstance(payload, dict) else None
    if not isinstance(stories, list):
        raise StoryError("Claude returned no story list.")
    groups = []
    for item in stories:
        if not isinstance(item, dict):
            continue
        post_ids = item.get("post_ids") or []
        if not isinstance(post_ids, list):
            continue
        groups.append(
            {
                "headline": " ".join(str(item.get("headline") or "").split())[:140],
                "summary": " ".join(str(item.get("summary") or "").split())[:360],
                "category": canonical_category(item.get("category")),
                "unverified": item.get("unverified") is not False,
                "post_ids": [str(post_id).strip() for post_id in post_ids if str(post_id).strip()],
            }
        )
    return groups


def assemble_stories(posts: list[dict], groups: list[dict]) -> list[dict]:
    """Rank groups from stored impressions and engagement. Drop ids the model invented."""
    by_id = {post["post_id"]: post for post in posts}
    claimed: set[str] = set()
    assembled = []
    for group in groups:
        if not group.get("headline") or not group.get("summary") or not group.get("category"):
            continue
        members = []
        for post_id in group.get("post_ids") or []:
            post = by_id.get(str(post_id))
            if post is None or post["post_id"] in claimed:
                continue
            claimed.add(post["post_id"])
            members.append(post)
        if not members:
            continue
        impressions = sum(post["impression_count"] or 0 for post in members)
        engagement = sum(post["engagement"] for post in members)
        chosen = sorted(
            members,
            key=lambda post: (post["engagement"], post["impression_count"] or 0, post["post_id"]),
            reverse=True,
        )[:3]
        assembled.append(
            {
                "headline": group["headline"],
                "summary": group["summary"],
                "category": group["category"],
                "unverified": bool(group.get("unverified", True)),
                "impressions": int(impressions),
                "engagement": int(engagement),
                "links": [_link(post) for post in chosen],
                "_rank_key": impressions + engagement,
            }
        )
    assembled.sort(key=lambda story: (story["_rank_key"], story["impressions"]), reverse=True)
    stories = []
    for rank, story in enumerate(assembled[:3], start=1):
        story.pop("_rank_key", None)
        story["rank"] = rank
        stories.append(story)
    return stories


def group_stored_posts(posts: list[dict], *, api_key: str, post=requests.post) -> GroupedStories:
    completion = _complete(posts, api_key=api_key, post=post)
    stories = assemble_stories(posts, completion["groups"])
    if not stories:
        raise StoryError("Claude did not return stories that match the stored posts.")
    return GroupedStories(
        stories=stories,
        model=completion["model"],
        input_tokens=completion["input_tokens"],
        output_tokens=completion["output_tokens"],
        estimated_cost_usd=completion["estimated_cost_usd"],
    )


def news_totals(stories: list[dict]) -> dict[str, dict]:
    totals: dict[str, dict] = {}
    for story in stories:
        bucket = totals.setdefault(
            story["date"],
            {"impressions": 0, "top_story_impressions": 0},
        )
        impressions = int(story["impressions"])
        bucket["impressions"] += impressions
        if impressions > bucket["top_story_impressions"]:
            bucket["top_story_impressions"] = impressions
    return totals


def _complete(posts: list[dict], *, api_key: str, post) -> dict:
    response = post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": STORY_MODEL,
            "max_tokens": 2000,
            "thinking": {"type": "disabled"},
            "output_config": {"effort": "low"},
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_user_prompt(posts)}],
        },
        timeout=60,
    )
    status = getattr(response, "status_code", 0)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if status != 200:
        detail = ""
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif payload.get("detail"):
            detail = str(payload.get("detail"))
        message = f"Claude request failed ({status})."
        if detail:
            message = f"{message} {' '.join(detail.split())[:300]}"
        if api_key:
            message = message.replace(api_key, "[redacted]")
        raise StoryError(message)
    text = _message_text(payload)
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return {
        "groups": parse_groups(text),
        "model": str(payload.get("model") or STORY_MODEL),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimate_cost(input_tokens, output_tokens),
    }


def _message_text(payload: dict) -> str:
    parts = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def _link(post: dict) -> dict:
    impressions = post["impression_count"]
    return {
        "id": post["post_id"],
        "url": f"https://x.com/i/web/status/{post['post_id']}",
        "engagement": int(post["engagement"]),
        "impressions": None if impressions is None else int(impressions),
        "text": post["text"][:180],
    }


def _metric_int(metrics: dict, name: str, default: int | None = None) -> int | None:
    value = metrics.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
