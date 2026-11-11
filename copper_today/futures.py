"""COMEX copper futures closes from Yahoo Finance."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from copper_today.config import FUTURES_PERIOD, FUTURES_TICKER
from copper_today.dates import to_iso_date

NEW_YORK = ZoneInfo("America/New_York")


def closes_from_history(frame) -> dict[str, float]:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    data = frame.copy()
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    close_name = next((column for column in data.columns if str(column).lower() == "close"), None)
    if close_name is None:
        return {}
    closes: dict[str, float] = {}
    for stamp, value in data[close_name].items():
        if pd.isna(value):
            continue
        day = to_iso_date(stamp)
        if day is None:
            continue
        closes[day] = round(float(value), 4)
    return closes


def completed_closes(closes: dict[str, float], now: datetime) -> tuple[dict[str, float], str]:
    """Drop the New York session that is still open. Its bar is a last trade, not a close."""
    open_day = now.astimezone(NEW_YORK).date().isoformat()
    return {day: price for day, price in closes.items() if day < open_day}, open_day


def fetch_copper_closes(
    *,
    ticker: str = FUTURES_TICKER,
    period: str = FUTURES_PERIOD,
) -> tuple[dict[str, float], str | None]:
    try:
        import yfinance as yf
    except ImportError:
        return {}, "yfinance is not installed."
    try:
        history = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    except Exception as exc:
        return {}, f"COMEX copper request failed: {' '.join(str(exc).split())[:400]}"
    closes = closes_from_history(history)
    if not closes:
        return {}, f"No daily closes returned for {ticker}."
    return closes, None
