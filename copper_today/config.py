"""Fixed sources, windows, and environment."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Recent search allows 512 characters. Retweets are excluded so the count
# is original posts and replies, not the same post counted again on every repost.
COPPER_QUERY = (
    '("copper price" OR "copper prices" OR "copper futures" OR "copper shortage" '
    'OR "COMEX copper" OR "LME copper" OR "copper market" OR "copper inventories" '
    'OR "copper inventory") lang:en -is:retweet'
)

# Google Trends compares at most five keywords in one request.
TRENDS_KEYWORDS = [
    "copper price",
    "copper shortage",
    "copper futures",
    "copper mining",
    "COMEX copper",
]
TRENDS_TIMEFRAME = "today 3-m"
TRENDS_GEO = ""

FUTURES_TICKER = "HG=F"
FUTURES_PERIOD = "6mo"

X_API_BASE = "https://api.x.com/2"
X_MAX_RESULTS = 100
RECENT_SEARCH_DAYS = 7

PERCENTILE_WINDOW_DAYS = 30
MIN_PERCENTILE_OBS = 10
MOVING_AVERAGE_POINTS = 7
ELEVATED_PERCENTILE = 75

METRICS = (
    "post_count",
    "impression_sum",
    "impression_median",
    "trends_interest",
    "copper_close",
)

# One grouping call per day. Haiku 5.5, low effort, thinking off.
# Prices checked 2026-11-14: https://platform.claude.com/docs/en/about-claude/pricing
STORY_MODEL = "claude-haiku-5-5"
STORY_INPUT_USD_PER_MTOK = 0.10
STORY_OUTPUT_USD_PER_MTOK = 0.50
STORY_LONG_INPUT_USD_PER_MTOK = 0.50
STORY_LONG_OUTPUT_USD_PER_MTOK = 2.50
STORY_LONG_CONTEXT_TOKENS = 100_000
STORY_CATEGORIES = (
    "copper price and macro",
    "mining and project news",
    "junior explorers",
    "policy and supply chain",
)

DEFAULT_ATTENTION_WEIGHT = 0.70
SCORE_MIN_DAYS = 7
SCORE_WINDOW_DAYS = 30
SCORE_GREEN_PERCENTILE = 75
SCORE_RED_PERCENTILE = 40


def load_env() -> None:
    """Load .env. A blank value already in the process does not block the file."""
    from dotenv import dotenv_values

    for key, value in dotenv_values(ROOT / ".env").items():
        if not value or not str(value).strip():
            continue
        current = os.environ.get(key, "")
        if not current.strip():
            os.environ[key] = value


def bearer_token() -> str:
    raw = os.environ.get("X_BEARER_TOKEN", "")
    return raw.strip().strip('"').strip("'")


def anthropic_api_key() -> str:
    raw = os.environ.get("ANTHROPIC_API_KEY", "")
    return raw.strip().strip('"').strip("'")


def get_db_path() -> Path:
    override = os.environ.get("COPPER_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return ROOT / "data" / "copper.db"
