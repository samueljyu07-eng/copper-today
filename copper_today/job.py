"""Collect one daily snapshot and refresh Trends and COMEX history."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from copper_today.config import COPPER_QUERY, FUTURES_TICKER, bearer_token, get_db_path, load_env
from copper_today.db import (
    clear_column,
    connect,
    delete_all_metrics,
    get_day,
    has_metrics,
    is_demo,
    set_meta,
    upsert_day,
)
from copper_today.demo import build_demo_rows
from copper_today.futures import completed_closes, fetch_copper_closes
from copper_today.trends import fetch_trends_basket
from copper_today.x_client import (
    SourceError,
    fetch_recent_count,
    fetch_top_posts,
    in_recent_window,
    summarize_impressions,
)


class JobError(RuntimeError):
    pass


@dataclass
class StepResult:
    name: str
    status: str
    detail: str


@dataclass
class JobResult:
    date: str
    steps: list[StepResult]
    replaced_demo: bool = False

    def exit_code(self) -> int:
        if any(step.status == "error" for step in self.steps):
            return 1
        if not any(step.status == "ok" for step in self.steps):
            return 1
        return 0

    def text(self) -> str:
        lines = [f"Copper attention collection for {self.date}"]
        if self.replaced_demo:
            lines.append("  removed synthetic demo rows")
        for step in self.steps:
            lines.append(f"  {step.name}: {step.status} ({step.detail})")
        return "\n".join(lines)


def run_job(
    db_path: Path,
    *,
    day: date | None = None,
    now: datetime | None = None,
    force_x: bool = False,
    skip_x: bool = False,
    demo: bool = False,
    replace_live: bool = False,
    token: str | None = None,
    trends_fetcher=None,
    futures_fetcher=None,
    count_fetcher=None,
    search_fetcher=None,
) -> JobResult:
    now = _as_utc(now or datetime.now(timezone.utc))
    if demo:
        return _run_demo(db_path, now=now, replace_live=replace_live)

    target = day or (now.date() - timedelta(days=1))
    if target > now.date():
        raise JobError("Collection date cannot be in the future.")

    trends_fetcher = trends_fetcher or fetch_trends_basket
    futures_fetcher = futures_fetcher or fetch_copper_closes
    token = bearer_token() if token is None else token.strip().strip('"').strip("'")

    trends_series, trends_error = trends_fetcher()
    futures_series, futures_error = futures_fetcher()
    open_day = None
    if futures_error is None:
        futures_series, open_day = completed_closes(futures_series or {}, now)
        if not futures_series:
            futures_error = f"No completed daily closes returned for {FUTURES_TICKER}."
            open_day = None
    steps: list[StepResult] = [
        _series_step("trends", trends_series, trends_error, "days"),
        _series_step("futures", futures_series, futures_error, "sessions"),
    ]

    with connect(db_path) as conn:
        existing = get_day(conn, target.isoformat())
        demo_now = is_demo(conn)

    x_steps, x_fields, x_note, write_note = _collect_x(
        target=target,
        now=now,
        token=token,
        skip_x=skip_x,
        force_x=force_x,
        existing=existing,
        count_fetcher=count_fetcher,
        search_fetcher=search_fetcher,
    )
    steps.extend(x_steps)

    trend_rows = {} if trends_error else dict(trends_series or {})
    future_rows = {} if futures_error else dict(futures_series or {})
    have_new = bool(trend_rows) or bool(future_rows) or bool(x_fields)
    replaced = False

    with connect(db_path) as conn:
        if demo_now and not have_new:
            steps.append(
                StepResult(
                    "database",
                    "skipped",
                    "Demo rows kept because every source failed.",
                )
            )
        else:
            if demo_now and have_new:
                delete_all_metrics(conn)
                replaced = True
            if trend_rows:
                clear_column(conn, "trends_interest")
                for row_day, value in trend_rows.items():
                    upsert_day(conn, row_day, {"trends_interest": float(value)})
            for row_day, value in future_rows.items():
                upsert_day(conn, row_day, {"copper_close": float(value)})
            if open_day is not None:
                _drop_open_session(conn, open_day)
            snapshot = {
                "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                **x_fields,
            }
            if write_note:
                snapshot["note"] = x_note
            upsert_day(conn, target.isoformat(), snapshot)
            set_meta(conn, "data_origin", "live")
        set_meta(
            conn,
            "last_job_json",
            json.dumps(
                {
                    "date": target.isoformat(),
                    "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "replaced_demo": replaced,
                    "steps": [asdict(step) for step in steps],
                }
            ),
        )

    return JobResult(date=target.isoformat(), steps=steps, replaced_demo=replaced)


def _run_demo(db_path: Path, *, now: datetime, replace_live: bool) -> JobResult:
    end = now.date() - timedelta(days=1)
    with connect(db_path) as conn:
        if has_metrics(conn) and not is_demo(conn) and not replace_live:
            raise JobError(
                "Refusing to replace collected rows with demo data. Pass --replace-live to overwrite them."
            )
        delete_all_metrics(conn)
        for row in build_demo_rows(end):
            day = row.pop("date")
            upsert_day(conn, day, row)
        set_meta(conn, "data_origin", "demo")
        steps = [StepResult("demo", "ok", f"90 synthetic days through {end.isoformat()}")]
        set_meta(
            conn,
            "last_job_json",
            json.dumps(
                {
                    "date": end.isoformat(),
                    "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "replaced_demo": False,
                    "steps": [asdict(step) for step in steps],
                }
            ),
        )
    return JobResult(date=end.isoformat(), steps=steps)


def _collect_x(
    *,
    target: date,
    now: datetime,
    token: str,
    skip_x: bool,
    force_x: bool,
    existing: dict | None,
    count_fetcher,
    search_fetcher,
) -> tuple[list[StepResult], dict, str | None, bool]:
    iso = target.isoformat()
    if skip_x:
        return [StepResult("x", "skipped", "X snapshot not requested.")], {}, None, False
    if not in_recent_window(target, now.date()):
        return (
            [
                StepResult(
                    "x",
                    "skipped",
                    f"{iso} is outside the last 7 days, so recent search and counts were not called.",
                )
            ],
            {},
            None,
            False,
        )
    if not token:
        message = "No X_BEARER_TOKEN in the environment."
        return [StepResult("x", "error", message)], {}, message, True

    count_fetcher = count_fetcher or (lambda day, moment: fetch_recent_count(token, COPPER_QUERY, day, moment))
    search_fetcher = search_fetcher or (lambda day, moment: fetch_top_posts(token, COPPER_QUERY, day, moment))
    steps: list[StepResult] = []
    fields: dict = {}
    notes: list[str] = []
    write_note = False

    if existing and existing.get("x_counts_ok") and not force_x:
        steps.append(StepResult("x counts", "skipped", "Already collected for this day."))
    else:
        try:
            count = int(count_fetcher(target, now))
        except (SourceError, Exception) as exc:
            steps.append(StepResult("x counts", "error", _clean(exc, token)))
        else:
            fields["post_count"] = count
            fields["x_counts_ok"] = 1
            steps.append(StepResult("x counts", "ok", f"{count} posts"))

    if existing and existing.get("x_search_ok") and not force_x:
        steps.append(StepResult("x search", "skipped", "Already collected for this day."))
    else:
        try:
            posts = list(search_fetcher(target, now) or [])
            summary = summarize_impressions(posts)
        except (SourceError, Exception) as exc:
            steps.append(StepResult("x search", "error", _clean(exc, token)))
        else:
            fields["posts_sampled"] = int(summary["posts_sampled"])
            fields["impression_sum"] = summary["impression_sum"]
            fields["impression_median"] = summary["impression_median"]
            fields["x_search_ok"] = 1
            missing = summary["impressions_missing"]
            if missing:
                noun = "post" if missing == 1 else "posts"
                notes.append(f"{missing} {noun} had no impression_count")
            median = summary["impression_median"]
            median_text = "n/a" if median is None else f"{median:,.1f}"
            steps.append(
                StepResult(
                    "x search",
                    "ok",
                    f"{summary['posts_sampled']} posts, median impressions {median_text}",
                )
            )
            write_note = True

    return steps, fields, "; ".join(notes) or None, write_note


def _drop_open_session(conn, open_day: str) -> None:
    row = get_day(conn, open_day)
    if not row:
        return
    upsert_day(conn, open_day, {"copper_close": None})
    row = get_day(conn, open_day)
    if row and all(row[name] is None for name in _VALUE_COLUMNS):
        conn.execute("DELETE FROM daily_metrics WHERE date = ?", (open_day,))


_VALUE_COLUMNS = (
    "post_count",
    "impression_sum",
    "impression_median",
    "posts_sampled",
    "trends_interest",
    "copper_close",
)


def _series_step(name: str, series: dict | None, error: str | None, unit: str) -> StepResult:
    if error:
        return StepResult(name, "error", error)
    count = len(series or {})
    if count == 0:
        return StepResult(name, "error", f"No {unit} returned.")
    noun = unit if count != 1 else unit[:-1]
    return StepResult(name, "ok", f"{count} {noun}")


def _clean(exc: Exception, token: str) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    if token:
        text = text.replace(token, "[redacted]")
    return text[:500]


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Store one day of copper attention metrics in SQLite.")
    parser.add_argument("--date", type=_parse_day, help="UTC day to snapshot (YYYY-MM-DD). Default: yesterday.")
    parser.add_argument("--force-x", action="store_true", help="Call X again even if this day was already collected.")
    parser.add_argument("--skip-x", action="store_true", help="Refresh Trends and COMEX only.")
    parser.add_argument("--demo", action="store_true", help="Load 90 synthetic days. Does not call any API.")
    parser.add_argument(
        "--replace-live",
        action="store_true",
        help="With --demo, overwrite rows that were collected from live sources.",
    )
    args = parser.parse_args(argv)
    try:
        result = run_job(
            get_db_path(),
            day=args.date,
            force_x=args.force_x,
            skip_x=args.skip_x,
            demo=args.demo,
            replace_live=args.replace_live,
        )
    except JobError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(result.text())
    return result.exit_code()


def _parse_day(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD.") from exc


if __name__ == "__main__":
    raise SystemExit(main())
