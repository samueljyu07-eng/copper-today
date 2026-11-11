from datetime import date, timedelta

from copper_today.analytics import prepare
from copper_today.charts import index_chart, series_chart


def _spike_rows():
    start = date(2026, 10, 1)
    rows = []
    for offset in range(21):
        value = 10 if offset < 20 else 100
        rows.append(
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "post_count": value,
                "impression_sum": value,
                "impression_median": value,
                "trends_interest": value,
                "copper_close": value,
            }
        )
    return rows


def test_index_chart_marks_flags_and_releases():
    result = prepare(_spike_rows())
    figure = index_chart(
        result.frame,
        result.index_p75,
        [{"date": "2026-10-15", "label": "Guidance", "id": 1}],
    )
    names = [trace.name for trace in figure.data]
    assert "Index" in names
    assert "7-day average" in names
    assert "Flagged" in names
    vertical = [shape for shape in figure.layout.shapes if shape.x0 == shape.x1]
    horizontal = [shape for shape in figure.layout.shapes if shape.y0 == shape.y1 and shape.x0 != shape.x1]
    assert len(vertical) == 1
    assert len(horizontal) == 1


def test_series_chart_has_average_and_marker():
    result = prepare(_spike_rows())
    figure = series_chart(
        result.frame,
        "post_count",
        "post_count_ma_7",
        "X post count",
        "Posts",
        ",.0f",
        [{"date": "2026-10-15", "label": "Guidance"}],
    )
    names = [trace.name for trace in figure.data]
    assert names == ["X post count", "7-day average"]
    vertical = [shape for shape in figure.layout.shapes if shape.x0 == shape.x1]
    assert len(vertical) == 1
