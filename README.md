# Copper Attention

Daily snapshot of copper attention and the COMEX price. One SQLite row per day, charted in Streamlit.

The Copper Attention Index is the average of 30-day percentile ranks for:

- X post volume for a fixed query, from `GET /2/tweets/counts/recent`
- the sum and median of `public_metrics.impression_count` on the top 100 posts from one recent-search request
- Google Trends interest, averaged across a five-keyword basket
- the COMEX copper futures close (`HG=F` on Yahoo Finance)

A day is flagged when the index is above the 75th percentile of stored index values, or higher than the previous index reading. Press-release dates are vertical lines you add in the sidebar.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Put an app-only bearer token in `.env`:

```
X_BEARER_TOKEN=your-token
```

`.env` stays on this machine. It is listed in `.gitignore`.

## Set a monthly X spend cap first

In the X Developer Console, set a spending limit for the billing cycle before the first collection. When the limit is reached, X blocks further API requests until the next cycle. This app cannot raise that cap.

A limit around **$25** covers one snapshot a day with room for a few reruns. Confirm the current rate card in the console. The rates published while this was built were $0.005 per recent-counts request and $0.005 per post read, about **$0.51** for a snapshot that returns 100 posts.

Each run that includes X makes only these calls:

- one `GET /2/tweets/counts/recent` (billed per request)
- one `GET /2/tweets/search/recent` with `max_results=100` and no pagination (billed per post returned, so at most 100 reads)

There are no user lookups and no full-archive search. The same UTC day is not sent to X again unless you ask to re-fetch it. X also deduplicates post reads inside a UTC day; the counts endpoint is still billed per request.

## Run

```bash
python -m copper_today.job
streamlit run app.py
```

The job defaults to **yesterday UTC**, so the post count is a finished day. Every run refreshes the Trends window and COMEX history. The current New York session is omitted until a later run, when Yahoo's bar is a completed close. Trends and futures do not use the X token. Trends numbers are relative to the window Google returns, and each successful pull replaces the stored search-interest column so the scale stays consistent.

```bash
python -m copper_today.job --date 2026-11-10
python -m copper_today.job --force-x
python -m copper_today.job --skip-x
```

Schedule it after 00:05 UTC:

```bash
15 0 * * * cd /Users/samuelyuwork/Desktop/copper-today && .venv/bin/python -m copper_today.job
```

The sidebar can run the same job. "Include the X snapshot" is on by default. Turn it off to refresh price and search interest without spending X credit. Today can be selected, and that post count is partial because the UTC day is still open.

## Demo

```bash
python -m copper_today.job --demo
```

Loads 90 synthetic days and does not call an API. The dashboard labels those rows. This refuses to overwrite collected data unless you pass `--replace-live`. The next live collection deletes the synthetic rows and keeps press-release markers.

## Tests

```bash
pytest
```

## Where the fixed sources live

The X query, Trends keywords, and futures ticker are in `copper_today/config.py`. The recent-search query has to stay within 512 characters, and the Trends basket cannot grow past five keywords.
