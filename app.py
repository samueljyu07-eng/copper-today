"""Streamlit dashboard for the Copper Attention Index."""

from __future__ import annotations

import html
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
    SCORE_GREEN_PERCENTILE,
    SCORE_MIN_DAYS,
    SCORE_RED_PERCENTILE,
    TRENDS_KEYWORDS,
    get_db_path,
    load_env,
)
from copper_today.dates import to_iso_date
from copper_today.db import (
    ValidationError,
    add_press_release,
    connect,
    delete_press_release,
    get_meta,
    list_press_releases,
    list_stories,
    list_story_runs,
    load_days,
    save_scores,
)
from copper_today.job import JobError, run_job
from copper_today.score import build_score_rows
from copper_today.stories import format_cost, news_totals

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
    try:
        with connect(path) as conn:
            rows = load_days(conn)
            markers = list_press_releases(conn)
            origin = get_meta(conn, "data_origin", "") or ""
            last_job = get_meta(conn, "last_job_json", "") or ""
            stories = list_stories(conn)
            story_runs = list_story_runs(conn)
    except sqlite3.Error as exc:
        st.error(f"The dashboard could not read the saved data. {exc}")
        return

    attention_weight = _sidebar(path, last_job)
    try:
        analytics = prepare(rows)
        frame = analytics.frame
    except Exception as exc:
        st.error(f"The attention index could not be computed from the saved rows. {exc}")
        analytics = None
        frame = pd.DataFrame()
    score_rows = build_score_rows(frame, news_totals(stories), attention_weight)
    try:
        with connect(path) as conn:
            save_scores(conn, score_rows)
    except sqlite3.Error as exc:
        st.error(f"The score could not be saved. The badge still uses the saved metrics. {exc}")

    _post_today(score_rows, frame)
    _job_errors(last_job)
    _trending(stories, story_runs)

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
    if frame.empty:
        if analytics is not None:
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
        st.markdown("**Stories and the post-today score**")
        st.markdown(
            "Stories are grouped from the posts already saved for that UTC day. "
            "The job makes one Claude call and does not read X again. "
            "Summaries stay inside the wording of those posts. "
            "The score blends the attention index with the combined impressions of the top three stories. "
            f"Green is at or above the {SCORE_GREEN_PERCENTILE}th percentile of the last "
            f"{PERCENTILE_WINDOW_DAYS} weekdays or weekends. "
            f"Red is at or below the {SCORE_RED_PERCENTILE}th percentile. "
            "The badge stays grey until seven days have an index."
        )


def _sidebar(path, last_job: str) -> float:
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
            with st.spinner(
                "Collecting Trends, the COMEX close, and the X snapshot if it is included. "
                "Stories use the posts from that snapshot."
            ):
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
            "one full snapshot was about $0.51. "
            "The story grouping is a separate Claude call, once per day, and its estimated cost is stored with that day."
        )
        st.divider()
        st.subheader("Post today?")
        attention_pct = st.slider(
            "Attention index weight",
            min_value=0,
            max_value=100,
            value=70,
            step=5,
            help="News flow gets the remaining share. The default is 70% attention index and 30% story impressions.",
        )
        st.caption(f"News flow weight: {100 - attention_pct}%.")
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
        return attention_pct / 100


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


_BADGE_COLORS = {
    "green": ("#1f6b3a", "#e7f5ec"),
    "yellow": ("#8a5a00", "#fff6dc"),
    "red": ("#8f2d1f", "#fdecea"),
    "calibrating": ("#5c564e", "#ece7df"),
}


def _post_today(score_rows: list[dict], frame: pd.DataFrame) -> None:
    latest = score_rows[-1] if score_rows else None
    if latest is None:
        _badge("calibrating", "—", "Calibrating: no index reading yet.")
        st.caption(
            f"Colour starts after {SCORE_MIN_DAYS} days have an index. "
            "Weekends are compared with weekends, and weekdays with weekdays."
        )
        return
    _badge(latest["color"], f"{latest['score']:.1f}", latest["reason"])
    kind = "weekends" if latest["day_type"] == "weekend" else "weekdays"
    st.caption(
        f"Reading for {latest['date']}, compared with other {kind}. "
        f"Green is the top {100 - SCORE_GREEN_PERCENTILE:.0f}% of the last 30 days. "
        f"Red is the bottom {SCORE_RED_PERCENTILE:.0f}%."
    )
    _history_strip(score_rows)
    with st.expander("Score breakdown"):
        _score_breakdown(frame, latest)


def _badge(color: str, score: str, reason: str) -> None:
    ink, fill = _BADGE_COLORS.get(color, _BADGE_COLORS["calibrating"])
    label = "Calibrating" if color == "calibrating" else color.capitalize()
    st.markdown(
        f"""
        <div style="border-radius:16px;padding:1.15rem 1.35rem;background:{fill};border:1px solid {ink};">
          <div style="font-size:0.8rem;letter-spacing:0.06em;text-transform:uppercase;color:{ink};font-weight:650;">
            Post today? · {html.escape(label)}
          </div>
          <div style="font-size:3.4rem;line-height:1.05;font-weight:700;color:{ink};margin:0.15rem 0 0.35rem;">
            {html.escape(score)}
          </div>
          <div style="font-size:1.05rem;color:#2a211c;">{html.escape(reason)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _history_strip(score_rows: list[dict]) -> None:
    latest = datetime.strptime(score_rows[-1]["date"], "%Y-%m-%d").date()
    start = latest - timedelta(days=29)
    window = [row for row in score_rows if datetime.strptime(row["date"], "%Y-%m-%d").date() >= start]
    if not window:
        return
    dots = []
    for row in window:
        ink, _fill = _BADGE_COLORS.get(row["color"], _BADGE_COLORS["calibrating"])
        title = html.escape(f"{row['date']} · {row['score']:.1f} · {row['color']}", quote=True)
        dots.append(
            f'<span title="{title}" style="width:14px;height:14px;border-radius:50%;background:{ink};display:inline-block;"></span>'
        )
    st.markdown(
        '<div style="display:flex;gap:6px;flex-wrap:wrap;margin:0.85rem 0 0.2rem;">'
        + "".join(dots)
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption("Last 30 days. Each dot is that day's colour.")


def _score_breakdown(frame: pd.DataFrame, score: dict) -> None:
    row = _frame_row(frame, score["date"])
    lines = []
    if row is not None:
        for key, title, _axis, fmt, _empty in SERIES:
            lines.append(
                {
                    "Component": title,
                    "Value": _num(row.get(key), fmt),
                    "30-day percentile": _num(row.get(f"pct_{key}"), ".0f"),
                    "Weight": "In the index",
                }
            )
    lines.append(
        {
            "Component": "Attention index",
            "Value": f"{score['attention_index']:.1f}",
            "30-day percentile": "—",
            "Weight": f"{score['attention_weight'] * 100:.0f}%",
        }
    )
    if score["news_impressions"] is None:
        news_value = "—"
    else:
        news_value = f"{int(score['news_impressions']):,}"
    lines.append(
        {
            "Component": "News flow (top 3 story impressions)",
            "Value": news_value,
            "30-day percentile": _num(score["news_flow"], ".0f"),
            "Weight": f"{score['news_weight'] * 100:.0f}%",
        }
    )
    lines.append(
        {
            "Component": "Post today score",
            "Value": f"{score['score']:.1f}",
            "30-day percentile": _num(score["score_percentile"], ".0f"),
            "Weight": "100%",
        }
    )
    if score["news_flow"] is None:
        st.caption("No story impressions for this day, so the score is the attention index.")
    elif score["news_weight"] == 0:
        st.caption("The news-flow weight is 0%, so the score is the attention index.")
    st.dataframe(pd.DataFrame(lines), hide_index=True, width="stretch")


def _frame_row(frame: pd.DataFrame, day: str):
    if frame is None or frame.empty or "date" not in getattr(frame, "columns", []):
        return None
    matched = None
    for record in frame.to_dict(orient="records"):
        if to_iso_date(record.get("date")) == day:
            matched = record
    return matched


def _trending(stories: list[dict], runs: list[dict]) -> None:
    st.subheader("Trending on X")
    st.caption("These are social media trends, not verified news.")
    by_date: dict[str, list[dict]] = {}
    for story in stories:
        by_date.setdefault(story["date"], []).append(story)
    runs_by_date = {run["date"]: run for run in runs}
    if not by_date:
        st.info(
            "No stories stored yet. The daily collection groups the posts it already fetched. "
            "That is one Claude call for the day, and it does not read X again."
        )
        _story_run_note(runs[-1] if runs else None)
        return
    dates = sorted(by_date, reverse=True)
    selected = dates[0]
    if len(dates) > 1:
        selected = st.selectbox("Story date", dates, index=0)
    else:
        st.caption(f"Story date {selected}")
    _story_run_note(runs_by_date.get(selected))
    for story in by_date[selected]:
        _story_card(story)


def _story_run_note(run: dict | None) -> None:
    if not run:
        return
    if run.get("status") == "error":
        st.error(f"stories: {run.get('detail')}")
        return
    cost = run.get("estimated_cost_usd")
    if cost is None:
        return
    tokens = f"{int(run.get('input_tokens') or 0)} in / {int(run.get('output_tokens') or 0)} out"
    model = run.get("model") or "Claude"
    st.caption(f"One Claude call for this day. Estimated {format_cost(float(cost))} ({tokens}, {model}).")


def _story_card(story: dict) -> None:
    st.markdown(f"**{int(story['rank'])}. {story['headline']}**")
    tags = [_pill(story["category"], "#9a5b2a", "#f4eadf")]
    if story.get("unverified"):
        tags.append(_pill("unverified", "#8f2d1f", "#fdecea"))
    st.markdown("".join(tags), unsafe_allow_html=True)
    st.write(story["summary"])
    st.caption(
        f"Combined impressions {int(story['impressions']):,}. "
        f"Engagement {int(story['engagement']):,} (likes, reposts, replies, quotes)."
    )
    for link in story.get("links") or []:
        url = _safe_post_url(link.get("url") or "")
        if url is None:
            continue
        excerpt = _excerpt(link.get("text") or "")
        engagement = int(link.get("engagement") or 0)
        st.markdown(f"- [{excerpt}]({url}) · engagement {engagement:,}")


def _pill(label: str, ink: str, fill: str) -> str:
    return (
        f'<span style="display:inline-block;margin:0 0.4rem 0.45rem 0;padding:0.12rem 0.5rem;'
        f"border-radius:999px;background:{fill};color:{ink};font-size:0.82rem;font-weight:650;\">"
        f"{html.escape(label)}</span>"
    )


def _excerpt(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).replace("[", "(").replace("]", ")")
    if len(cleaned) > 140:
        return cleaned[:137] + "..."
    return cleaned or "View post"


def _safe_post_url(url: str) -> str | None:
    prefix = "https://x.com/i/web/status/"
    if not url.startswith(prefix):
        return None
    post_id = url[len(prefix) :]
    if not post_id.isdigit():
        return None
    return url


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
