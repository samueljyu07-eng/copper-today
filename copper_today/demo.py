"""Synthetic rows so the dashboard can be previewed before any API call."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np


def build_demo_rows(end: date, days: int = 90) -> list[dict]:
    rng = np.random.default_rng(11)
    wave = np.sin(np.linspace(0, 4 * np.pi, days))
    rows = []
    for index in range(days):
        day = end - timedelta(days=days - 1 - index)
        spike = 1.0 if index == days - 1 else 0.0
        level = 1 + 0.15 * float(wave[index]) + float(rng.normal(0, 0.04))
        level = max(level, 0.2)
        rows.append(
            {
                "date": day.isoformat(),
                "post_count": int(800 * level + 5000 * spike),
                "impression_sum": int(250_000 * level + 2_000_000 * spike),
                "impression_median": round(400 * level + 3000 * spike, 2),
                "posts_sampled": 100,
                "trends_interest": round(min(100.0, 30 * level + 45 * spike), 2),
                "copper_close": round(4.6 * level + 1.2 * spike, 4),
                "x_counts_ok": 1,
                "x_search_ok": 1,
            }
        )
    return rows
