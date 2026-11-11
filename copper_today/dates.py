"""Calendar-date parsing shared by the source clients."""

from __future__ import annotations

import pandas as pd


def to_iso_date(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
        return None
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:
            return None
    text = str(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None
