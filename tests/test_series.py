import pandas as pd

from copper_today.config import TRENDS_KEYWORDS
from copper_today.futures import closes_from_history
from copper_today.trends import basket_from_frame


def test_trends_basket_stays_within_one_request():
    assert 1 <= len(TRENDS_KEYWORDS) <= 5


def test_basket_averages_keywords_and_drops_the_partial_period():
    frame = pd.DataFrame(
        {
            "copper price": [10, 40],
            "copper shortage": [30, 60],
            "isPartial": [False, True],
        },
        index=pd.to_datetime(["2026-11-09", "2026-11-10"]),
    )
    assert basket_from_frame(frame, ["copper price", "copper shortage"]) == {"2026-11-09": 20.0}


def test_basket_averages_hours_into_one_day():
    frame = pd.DataFrame(
        {"copper price": [10, 30]},
        index=pd.to_datetime(["2026-11-09 00:00", "2026-11-09 01:00"]),
    )
    assert basket_from_frame(frame, ["copper price"]) == {"2026-11-09": 20.0}


def test_basket_without_matching_columns_is_empty():
    frame = pd.DataFrame({"other": [1]}, index=pd.to_datetime(["2026-11-09"]))
    assert basket_from_frame(frame, ["copper price"]) == {}


def test_closes_from_yahoo_multiindex():
    columns = pd.MultiIndex.from_tuples([("Close", "HG=F"), ("Volume", "HG=F")])
    frame = pd.DataFrame(
        [[5.51234, 10], [None, 11]],
        index=pd.to_datetime(["2026-11-10", "2026-11-11"]),
        columns=columns,
    )
    assert closes_from_history(frame) == {"2026-11-10": 5.5123}
