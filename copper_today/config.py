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


def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)


def bearer_token() -> str:
    raw = os.environ.get("X_BEARER_TOKEN", "")
    return raw.strip().strip('"').strip("'")


def get_db_path() -> Path:
    override = os.environ.get("COPPER_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return ROOT / "data" / "copper.db"
