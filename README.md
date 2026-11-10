# Income ETF Dashboard

A self-hosted dashboard that ranks the covered-call / high-income ETFs from the
[@passiveincomeinvesting](https://www.youtube.com/@passiveincomeinvesting) YouTube channel by **total return,
distribution stability and yield**, next to **what the host actually said about each fund** (with video links, dates and
recency weighting), his investing mindset, and a recency-weighted video library.

* **Backend:** FastAPI + SQLite + APScheduler (Python 3.12)  **Frontend:** React (Vite) + Recharts, served by nginx
* **Data:** prices and distributions from Yahoo Finance via `yfinance` (no API key); videos via `yt-dlp` and
  `youtube-transcript-api` (no API key, no paid service). **No secrets are needed anywhere.**
* **Universe:** 189 ranked funds (106 CAD listings + 83 USD listings) and 44 benchmark/comparison funds, built from the
  channel's monthly ranking videos and checked against Yahoo Finance. Fully editable in `config/tickers.yaml`.

> Educational use only, not financial advice. Yahoo data is unofficial and can be delayed, wrong or missing. The opinions
> shown are the creator's, quoted or paraphrased from auto-generated captions (spelling errors included); always check the
> linked video for context.

## Quick start

```bash
cp .env.example .env        # optional: ports / bind address / timezone
docker compose up -d
open http://localhost:8080  # first start downloads full history for ~230 symbols (about a minute), then the table fills in
```

The UI binds to `127.0.0.1` by default because there is no login. Set `BIND_ADDR=0.0.0.0` in `.env` to share it on your
network. `config/` is mounted into the container, so edit `config/tickers.yaml` / `config/settings.yaml` and run
`docker compose restart backend`. SQLite, transcripts and UI-saved settings live in the `appdata` volume.

### Without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt
(cd backend && uvicorn app.main:app --port 8000)            # API + scheduler, data in ./data
(cd frontend && npm ci && npm run dev)                      # http://localhost:5173 (proxies /api to :8000)
(cd backend && python -m pytest -q)                         # offline tests, no network
```

## What you see

| Page | Contents |
|---|---|
| **Ranking** | Score, 1M/3M/6M/1Y/3Y/since-inception **total return** (or price return, toggle), TTM yield, distribution trend and cuts, return vs benchmark, his latest stance. Filters: CAD/USD/both, category, search, "only funds he has an opinion on". Click a row for the total-return-vs-price chart, **distribution history chart** (cuts in red), the numbers behind the score, his stance (+ superseded older opinions) and his latest mentions. Sliders change the weights live; "Save as default" persists them. |
| **His stance** | Every fund with a dated opinion: label, date, recency weight, verbatim caption quote and a deep link to the moment in the video. |
| **Video library** | All 939 videos on the channel's Videos tab sorted by a recency-weighted score, with the funds each transcript mentions. |
| **Mindset & checklist** | His 10 Commandments, the covered-call ETF checklist in his order, key ideas, how he defines total return, and the "9 things I'd never invest in" list, each with source link. |

## How the ranking works

`score = Σ weight × percentile` where each component is a **percentile rank (0–100) across the funds**, so a score of 80
means "better than about 80% of the universe on the weighted mix". Weights are re-normalised to sum to 1.

| Component | Default weight | Definition |
|---|---|---|
| **Total return** | **0.45** | Over the chosen window (default **1Y**; 1M, 3M, 6M, 1Y, 3Y or since inception). Every distribution is reinvested at the close of its ex-date (DRIP approximation, the same idea as stockanalysis.com's total return). |
| **Stability** | **0.30** | Mean of four sub-percentiles (lower is better for all four): **cuts** 0.35 (summed size of cuts in the last 12 months), **payment variability** 0.15 (CV of monthly totals), **volatility** 0.25 (annualised, 1Y, of the total-return series), **max drawdown** 0.25 (1Y, total-return series). |
| **Yield** | **0.25** | Trailing-12-month yield = distributions in the last 365 days ÷ latest close, **computed from dividends** (not Yahoo's `yield` field). Funds with under a year of history are annualised and marked `*`. |

Why these defaults: the host's own rule is that *"yield is not total return"* and that total return is the ultimate
measure, so return carries the most weight; he judges income funds by whether they keep paying, hence stability second;
yield is a real part of an income investor's goal but the least reliable number, hence last. Change them on the page
(sliders, instant) or in `config/settings.yaml`; **Save as default** writes `data/settings.override.yaml`, **Reset**
deletes it.

Details that matter:

* **Distribution cuts are measured on monthly totals**, not per payment, so weekly payers (QDTE, XDTE, TDAX, YieldMax-style funds)
  whose individual payments jump around are not flagged as "cut" every week. Weekly/bi-weekly payers use a 3-month rolling
  mean of monthly totals and ignore the incomplete current month; quarterly payers are compared payment to payment.
  A cut is a period more than `cut_threshold` (10%) below the previous one.
* **Funds too young for the chosen window** (e.g. no 1Y history) get a neutral return component of 50 and a `*` instead of
  being rewarded for having no track record (`missing_return`: `neutral` | `renormalize` | `exclude`).
* **Stale funds** (last price more than 10 days older than the newest price, i.e. halted/delisted) are left unranked.
* **Scope**: percentiles against all funds, or only against the fund's own category (categories with fewer than 3 scorable funds fall back to all).
* **Benchmark-relative** = fund total return − its benchmark's total return over the same window. Benchmarks must have the
  **same currency as the listing** (a `-U.TO` fund is compared with a USD benchmark such as VOO/QQQ, never with VFV.TO),
  which is enforced by a test. Pairings follow his own (HHIS→QQC, USCL→VFV, QQCL→QQC, HYLD→VSP (CAD-hedged S&P 500), HYLD-U→VOO...);
  some categories have no sensible benchmark and show "–".

## "His stance" and recency weighting

* A stance is a **dated, sourced opinion** (`label`, quote, video, timestamp) in `config/tickers.yaml`. **The newest dated stance wins**
  per ticker; older ones stay visible as history in the fund detail. Labels: core, love, hold, buy, recommend, positive, top, watch, neutral, cautious, avoid, sell, obsolete.
* Quotes are extracted from the transcripts by regex (`tools/specs/stance_specs.yaml`) and copied verbatim; the tool fails if a pattern doesn't match.
* **Coverage is partial on purpose**: stances only exist where the transcript was read and the opinion is explicit (25 funds plus their USD twins, 29 dated entries
  at the time of writing; two more, QDAY and HBTE, are attached to the `unresolved:` list). Every other fund shows its **latest mention** (auto-extracted snippet + link) instead of an invented opinion.
* **Recency decay**: `weight = 0.5 ^ (age_days / half_life)`, default half-life **180 days** (adjustable on the Ranking, His stance and
  Video library pages, and in `config/settings.yaml`). It is displayed with each stance and drives the video library:
  `video score = log10(1 + views) × weight` (so one old viral video does not bury recent ones; switch to linear views or ignore views in the UI).
  Older videos are kept; undated (probably members-only) videos get a neutral weight of 0.25.

## Data refresh

| Job | Schedule (America/Toronto, editable) | What it does |
|---|---|---|
| `prices` | every 30 min, Mon–Fri 09:00–16:30 | last 5 days of closes + dividends for every symbol |
| `dividends` | nightly 02:30 | last 2 years of dividends (catches late/amended ones) |
| `videos` | weekly, Sunday 03:00 | lists the channel with yt-dlp, dates new videos, fetches transcripts with `youtube-transcript-api`, tags fund mentions. At most 25 new videos per run; stops cleanly if YouTube rate-limits the IP |
| `full` | on first start (empty DB) or `POST /api/jobs/full/run` | full history download, in chunks of 40 symbols with pauses and retry/back-off |

Run any job by hand: `POST /api/jobs/{prices|dividends|videos|full}/run` (also the "refresh prices" link in the UI). `GET /api/status` shows
recent runs, next runs and symbols Yahoo returned nothing for. New videos update mentions and the library automatically; **new stances are
not auto-detected** (that would be guessing): add them to `config/tickers.yaml` under the fund's `stances:`.

## Configuration files

* `config/tickers.yaml` – the universe: per symbol `ticker`, `name`, `listing` (CAD/USD), `category`, `role` (`fund` ranked | `benchmark` fetched but not ranked), `benchmark`, `pair` (other listing of the same fund), `aliases` (how captions spell it), `stances`.
  Also `categories`, `excluded` (symbols deliberately left out, with the reason) and `unresolved` (funds he discusses that Yahoo doesn't have).
* `config/settings.yaml` – weights, window, scope, half-life, schedule, fetch pacing (all commented).
* `seed/` – shipped research data: `videos.json` (titles/dates/views/mentions per video), `mentions.json` (latest snippets), `mindset.json`. Raw transcripts are **not** in the repo.
* `tools/` – regenerate the above from a raw research folder: `python tools/build_seed.py --raw <folder>`; `tools/verify_symbols.py` checks symbols against Yahoo (cached, rate-limit aware).

## Known limits

* Yahoo (via yfinance) is unofficial: symbols can vanish, new listings can lack history, the first download can be rate-limited (it retries with back-off; re-run `full` later).
* Total return is a model (reinvest at ex-date close), not your broker's number; Canadian funds' return of capital / tax treatment is ignored.
* Percentile scores are **relative** to this universe, not an absolute quality rating, and past performance does not predict anything.
* Caption-based fund detection has false positives/negatives; aliases are in `tickers.yaml`. A dozen funds he discusses (e.g. the new Hamilton "DayMax" QDAY/CDAY, Harvest HBTE/HBTI) have no Yahoo symbol and are listed under `unresolved:` with his stance where known.
* yt-dlp prints a JavaScript-runtime warning on YouTube; harmless here because only metadata is read.
* Single uvicorn worker on purpose (the scheduler runs in-process).

## API

`/api/funds` (ranked, query overrides `w_total_return,w_stability,w_yield,window,scope,half_life_days,category,listing,benchmarks`), `/api/funds/{ticker}`,
`/api/funds/{ticker}/distributions`, `/api/funds/{ticker}/history`, `/api/categories`, `/api/stances`, `/api/videos`, `/api/mindset`,
`GET/PUT/DELETE /api/settings`, `/api/status`, `/api/health`, `POST /api/jobs/{name}/run`. Interactive docs at `/api/docs`.

## Layout

```
backend/app   config, db, metrics (pure), scoring (pure), fetch (yfinance), videos (yt-dlp), mentions, jobs, service, main
backend/tests offline pytest suite (synthetic series, fake provider, TestClient)
frontend/src  React pages: Ranking, Stances, Videos, Mindset + FundDetail
config/       tickers.yaml, settings.yaml          seed/  shipped research JSON          tools/  seed builders
docker-compose.yml  backend/Dockerfile  frontend/Dockerfile  frontend/nginx.conf  .env.example
```

No license file is included yet.
