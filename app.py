"""Streamlit dashboard for the Copper Attention Index."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from copper_today.analytics import prepare
from copper_today.charts import index_chart, series_chart
from copper_today.config import (
    COPPER_QUERY,
    FUTURES_TICKER,
    METRICS,
    MIN_PERCENTILE_OBS,
    PERCENTILE_WINDOW_DAYS,
    TRENDS_KEYWORDS,
    get_db_path,
    load_env,
)
from copper_today.db import (
    ValidationError,
    add_press_release,
    connect,
    delete_press_release,
    get_meta,
    list_press_releases,
    load_days,
)
from copper_today.job import JobError, run_job

SERIES = (
    ("post_count", "X post count", "Posts", ",.0f", "No X post counts yet."),
    (
        "impression_sum",
        "Impression sum",
        "Impressions",
        ",.0f",
        "No impression sums yet.",
    ),
    (
        "impression_median",
        "Impression median",
        "Impressions",
        ",.1f",
        "No impression medians yet.",
    ),
    (
        "trends_interest",
        "Search interest",
        "Interest (0–100)",
        ",.1f",
        "No Google Trends values yet.",
    ),
    (
        "copper_close",
        "COMEX copper close",
        "USD per lb",
        ",.4f",
        "No COMEX closes yet.",
    ),
)

def main() -> None:
    load_env()
    st.set_page_config(page_title="Copper Attention", layout="wide")
    path = get_db_path()
    with connect(path) as conn:
        rows = load_days(conn)
        markers = list_press_releases(conn)
        origin = get_meta(conn, "data_origin", "") or ""
        last_job = get_meta(conn, "last_job_json", "") or ""

    _sidebar(path, last_job)
    analytics = prepare(rows)
    frame = analytics.frame

    st.title("Copper Attention")
    st.caption(
        "One row per day. The index averages 30-day percentile ranks for X post volume, "
        "the sum and median impressions of the top 100 posts, Google Trends interest, "
        "and the COMEX copper close."
    )
    if origin == "demo":
        st.warning(
            "These rows are synthetic demo data. Run the daily collection to replace them. "
            "A live run deletes the demo rows and keeps press-release markers."
        )
    _job_errors(last_job)

    if frame.empty:
        st.info(
            "No rows yet. Run the daily collection from the sidebar. "
            "It backfills Trends and COMEX history, and calls X when a bearer token is set."
        )
    else:
        _headline(frame)
        _flagged_table(frame)
        _charts(frame, analytics.index_p75, markers)
        _table(frame)

    with st.expander("Sources and method"):
        st.markdown("**X query**")
        st.code(COPPER_QUERY, language="text")
        st.markdown(
            "Post counts use `GET /2/tweets/counts/recent` for that UTC day. "
            "The impression sum and median come from one recent-search page: "
            "`sort_order=relevancy`, `max_results=100`, no pagination. "
            "If X rejects relevancy sort, the job retries once with recency. "
            "Impressions are the public counts on those posts at collection time."
        )
        st.markdown("**Google Trends basket**")
        st.write(", ".join(TRENDS_KEYWORDS))
        st.markdown(
            "The stored value is the average of those keywords. "
            "Google normalizes the basket to the pulled window, so a later collection "
            "replaces the search-interest column with the new scale."
        )
        st.markdown("**Futures**")
        st.write(f"COMEX copper continuous future `{FUTURES_TICKER}`, daily close from Yahoo Finance. The current New York session is left out until a later run, so a live last-trade is not stored as the close.")
        st.markdown(
            f"Each component's percentile uses the prior {PERCENTILE_WINDOW_DAYS} calendar days "
            f"and needs at least {MIN_PERCENTILE_OBS} values. "
            "The sample minimum maps to 0, the maximum maps to 100, and ties land in the middle, "
            "so a flat series sits near 50. The index is the average of the percentiles that exist that day. "
            "The elevated line is the 75th percentile of the index values on this page. "
            "It is recomputed on load, so earlier flags can change as history grows. "
            "Rising means the index is higher than the previous index reading. "
            "The dotted line on each chart is the mean of the last seven stored values of that series. "
            "X dates are UTC days. The futures close uses the COMEX session date."
        )


def _sidebar(path, last_job: str) -> None:
    today = datetime.now(timezone.utc).date()
    with st.sidebar:
        st.header("Daily collection")
        picked = st.date_input(
            "Snapshot date (UTC)",
            value=today - timedelta(days=1),
            max_value=today,
        )
        if picked == today:
            st.caption("This UTC day is still open, so the post count will be partial.")
        include_x = st.checkbox(
            "Include the X snapshot",
            value=True,
            help="One counts request and one search of at most 100 posts.",
        )
        force_x = st.checkbox("Re-fetch X if this day was already collected", value=False)
        if st.button("Run daily collection", type="primary"):
            with st.spinner("Collecting Trends, the COMEX close, and the X snapshot if it is included."):
                try:
                    run_job(
                        path,
                        day=picked,
                        skip_x=not include_x,
                        force_x=force_x,
                    )
                except JobError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        _last_job(last_job)
        st.divider()
        st.subheader("Spend")
        st.caption(
            "Each X snapshot is one recent-counts request plus a single search that stops at 100 posts. "
            "Set a monthly spending limit in the X Developer Console. "
            "When that limit is reached, X blocks further requests until the next cycle. "
            "A cap around $25 covers a daily snapshot with room for a few reruns. "
            "Confirm current rates in the console. At the rates published while this was built, "
            "one full snapshot was about $0.51."
        )
        st.divider()
        st.subheader("Press releases")
        with st.form("add_marker", clear_on_submit=True):
            marker_date = st.date_input("Press release date", value=today)
            marker_label = st.text_input("Label", max_chars=80, placeholder="Q3 production release")
            submitted = st.form_submit_button("Add marker")
        if submitted:
            try:
                with connect(path) as conn:
                    add_press_release(conn, marker_date.isoformat(), marker_label)
            except ValidationError as exc:
                st.error(str(exc))
            except sqlite3.IntegrityError:
                st.error("That date and label are already marked.")
            else:
                st.rerun()
        with connect(path) as conn:
            releases = list_press_releases(conn)
        if not releases:
            st.caption("No press releases yet. Markers are vertical lines on every chart.")
        for release in releases:
            st.write(f"{release['date']}  {release['label']}")
            if st.button("Remove", key=f"remove-release-{release['id']}"):
                with connect(path) as conn:
                    delete_press_release(conn, release["id"])
                st.rerun()


def _last_job(last_job: str) -> None:
    if not last_job:
        return
    try:
        payload = json.loads(last_job)
    except json.JSONDecodeError:
        return
    st.caption(f"Last run {payload.get('date', '')} at {payload.get('at', '')} UTC")
    for step in payload.get("steps") or []:
        st.caption(f"{step.get('name')}: {step.get('detail')}")


def _job_errors(last_job: str) -> None:
    if not last_job:
        return
    try:
        payload = json.loads(last_job)
    except json.JSONDecodeError:
        return
    errors = [step for step in payload.get("steps") or [] if step.get("status") == "error"]
    for step in errors:
        st.error(f"{step.get('name')}: {step.get('detail')}")


def _headline(frame: pd.DataFrame) -> None:
    indexed = frame.dropna(subset=["index_value"])
    if indexed.empty:
        st.info(
            f"The index appears once a metric has at least {MIN_PERCENTILE_OBS} values "
            f"inside a {PERCENTILE_WINDOW_DAYS}-day window."
        )
        latest = frame.iloc[-1]
    else:
        latest = indexed.iloc[-1]
    reading_date = pd.Timestamp(latest["date"]).strftime("%Y-%m-%d")
    st.caption(f"Reading for {reading_date}")
    if not indexed.empty and latest["flag"]:
        st.markdown(f"**{latest['flag']}**")
    if not indexed.empty:
        components = int(latest["index_components"])
        st.caption(f"{components} of {len(METRICS)} components in this index reading")

    previous = latest["index_previous"] if "index_previous" in latest else None
    delta = None
    if not indexed.empty and pd.notna(previous):
        delta = f"{latest['index_value'] - previous:+.1f} vs prior reading"

    first = st.columns(3)
    second = st.columns(3)
    first[0].metric(
        "Index",
        _num(latest.get("index_value"), ",.1f"),
        delta=delta,
        delta_color="off",
        help="Average of the component percentiles for this day.",
    )
    first[1].metric("Post count", _num(latest.get("post_count"), ",.0f"), help="Matching posts in that UTC day.")
    first[2].metric(
        "Impression sum",
        _num(latest.get("impression_sum"), ",.0f"),
        help="Sum of impression_count on the top 100 posts.",
    )
    second[0].metric(
        "Impression median",
        _num(latest.get("impression_median"), ",.1f"),
        help="Median impression_count on the top 100 posts.",
    )
    second[1].metric(
        "Search interest",
        _num(latest.get("trends_interest"), ",.1f"),
        help="Average Google Trends interest for the keyword basket.",
    )
    second[2].metric(
        "Copper close",
        _num(latest.get("copper_close"), ",.4f"),
        help=f"COMEX {FUTURES_TICKER} daily close, USD per pound.",
    )

    x_rows = frame.loc[frame["post_count"].notna()]
    if not x_rows.empty:
        last_x = pd.Timestamp(x_rows.iloc[-1]["date"]).strftime("%Y-%m-%d")
        if last_x != reading_date:
            st.caption(f"Post counts and impressions were last stored on {last_x}.")
    elif frame["post_count"].isna().all():
        st.caption("Post counts and impressions are empty until a collection runs with X_BEARER_TOKEN.")


def _flagged_table(frame: pd.DataFrame) -> None:
    st.subheader("Flagged days")
    choice = st.radio(
        "Show",
        ["Above 75th percentile or rising", "Above 75th percentile", "Rising"],
        horizontal=True,
        label_visibility="collapsed",
    )
    flagged = frame.loc[frame["flagged"]].copy()
    if choice == "Above 75th percentile":
        flagged = flagged.loc[flagged["elevated"]]
    elif choice == "Rising":
        flagged = flagged.loc[flagged["rising"]]
    if flagged.empty:
        st.caption("No days match this flag yet.")
        return
    table = flagged.sort_values("date", ascending=False)
    st.dataframe(
        _view(
            table,
            {
                "date": "Date",
                "index_value": "Index",
                "flag": "Flag",
                "index_components": "Components",
            },
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Index": st.column_config.NumberColumn(format="%.1f"),
            "Components": st.column_config.NumberColumn(format="%d"),
        },
    )


def _charts(frame: pd.DataFrame, p75, markers: list[dict]) -> None:
    st.subheader("Copper Attention Index")
    st.caption(
        "Diamonds mark days above the index's 75th percentile or higher than the previous reading. "
        "Dashed lines are press releases. The green dotted line is the 7-day average."
    )
    if frame["index_value"].notna().any():
        _show(index_chart(frame, p75, markers))
    else:
        st.caption("No index yet.")

    st.subheader("Components")
    st.caption("The dotted line is the mean of the last seven stored values of that series.")
    for column, name, ytitle, yformat, empty in SERIES:
        st.markdown(f"**{name}**")
        if column not in frame or frame[column].notna().sum() == 0:
            st.caption(empty)
            continue
        _show(
            series_chart(
                frame,
                column,
                f"{column}_ma_7",
                name,
                ytitle,
                yformat,
                markers,
            )
        )


def _table(frame: pd.DataFrame) -> None:
    st.subheader("Daily rows")
    table = frame.sort_values("date", ascending=False)
    columns = {
        "date": "Date",
        "index_value": "Index",
        "flag": "Flag",
        "index_components": "Components",
        "post_count": "Post count",
        "posts_sampled": "Posts sampled",
        "impression_sum": "Impression sum",
        "impression_median": "Impression median",
        "trends_interest": "Search interest",
        "copper_close": "Copper close",
        "note": "Note",
    }
    st.dataframe(
        _view(table, columns),
        hide_index=True,
        width="stretch",
        height=420,
        column_config={
            "Index": st.column_config.NumberColumn(format="%.1f"),
            "Components": st.column_config.NumberColumn(format="%d"),
            "Post count": st.column_config.NumberColumn(format="%d"),
            "Posts sampled": st.column_config.NumberColumn(format="%d"),
            "Impression sum": st.column_config.NumberColumn(format="%d"),
            "Impression median": st.column_config.NumberColumn(format="%.1f"),
            "Search interest": st.column_config.NumberColumn(format="%.1f"),
            "Copper close": st.column_config.NumberColumn(format="%.4f"),
        },
    )
    with st.expander("Component percentiles"):
        pct = {
            "date": "Date",
            "pct_post_count": "Post count",
            "pct_impression_sum": "Impression sum",
            "pct_impression_median": "Impression median",
            "pct_trends_interest": "Search interest",
            "pct_copper_close": "Copper close",
        }
        st.dataframe(
            _view(table, pct),
            hide_index=True,
            width="stretch",
            column_config={
                name: st.column_config.NumberColumn(format="%.1f")
                for name in pct.values()
                if name != "Date"
            },
        )


def _view(frame: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    present = {key: label for key, label in columns.items() if key in frame.columns}
    view = frame.loc[:, list(present)].rename(columns=present).copy()
    if "Date" in view.columns:
        view["Date"] = pd.to_datetime(view["Date"]).dt.strftime("%Y-%m-%d")
    if "Note" in view.columns:
        view["Note"] = view["Note"].fillna("")
    return view


def _show(figure) -> None:
    st.plotly_chart(figure, width="stretch")


def _num(value, spec: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    return format(float(value), spec)


main()
