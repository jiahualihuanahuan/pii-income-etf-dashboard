"""Refresh jobs + APScheduler wiring."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from . import db, seed, service, videos
from .config import load_settings, load_universe
from .fetch import YahooProvider

log = logging.getLogger("jobs")
_running: dict[str, threading.Lock] = {}


def _tickers(universe: dict) -> list[str]:
    return [f["ticker"] for f in universe["funds"]]


def refresh_market(provider, period: str, name: str, tickers: Optional[list[str]] = None) -> dict:
    """Download `period` of history for the universe and upsert closes + dividends."""
    universe = load_universe()
    tickers = tickers or _tickers(universe)
    got = provider.download(tickers, period)
    n_p = n_d = 0
    with db.session() as con:
        for t, (close, divs) in got.items():
            n_p += db.upsert_prices(con, t, close)
            n_d += db.upsert_dividends(con, t, divs)
            con.execute("INSERT INTO fund_meta(ticker,last_price_fetch,last_div_fetch) VALUES (?,?,?) "
                        "ON CONFLICT(ticker) DO UPDATE SET last_price_fetch=excluded.last_price_fetch, last_div_fetch=excluded.last_div_fetch, error=NULL",
                        (t, datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")))
        for t in set(tickers) - set(got):
            con.execute("INSERT INTO fund_meta(ticker,error) VALUES (?,?) ON CONFLICT(ticker) DO UPDATE SET error=excluded.error", (t, "no data returned"))
    return {"requested": len(tickers), "received": len(got), "price_rows": n_p, "dividend_rows": n_d}


def run_job(name: str, provider=None, **kw) -> dict:
    """name: full | prices | dividends | videos. Serialised per name; records a job_runs row."""
    lock = _running.setdefault(name, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"skipped": "already running"}
    jid = db.start_job(name)
    try:
        s = load_settings()
        f = s["fetch"]
        prov = provider or YahooProvider(f["chunk_size"], f["pause_seconds"], f["retries"])
        if name == "full":
            res = refresh_market(prov, "max", name)
        elif name == "prices":
            res = refresh_market(prov, "5d", name)
        elif name == "dividends":
            res = refresh_market(prov, "2y", name)
        elif name == "videos":
            res = videos.sync(s, load_universe(), **kw)
        else:
            raise ValueError(f"unknown job {name}")
        try:  # warm the metrics cache so the first page load after a refresh is fast
            service.all_metrics(load_universe(), s["cut_threshold"])
        except Exception:
            log.exception("cache warm-up failed")
        db.finish_job(jid, "ok", str(res))
        return res
    except Exception as e:  # keep the scheduler alive
        log.exception("job %s failed", name)
        db.finish_job(jid, "error", repr(e))
        return {"error": repr(e)}
    finally:
        lock.release()


def run_async(name: str) -> None:
    threading.Thread(target=run_job, args=(name,), daemon=True, name=f"job-{name}").start()


def build_scheduler(settings: dict):
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    tz = settings["timezone"]
    sch = BackgroundScheduler(timezone=tz)
    sc = settings["schedule"]
    sch.add_job(run_job, CronTrigger(timezone=tz, **sc["prices"]), args=["prices"], id="prices", max_instances=1, coalesce=True)
    sch.add_job(run_job, CronTrigger(timezone=tz, **sc["dividends"]), args=["dividends"], id="dividends", max_instances=1, coalesce=True)
    sch.add_job(run_job, CronTrigger(timezone=tz, **sc["videos"]), args=["videos"], id="videos", max_instances=1, coalesce=True)
    return sch


def bootstrap() -> None:
    """On first start: load seed JSON, and kick off a full history download if the DB has no prices."""
    db.init()
    seed.load()
    with db.session() as con:
        empty = db.count_prices(con) == 0
    if empty and load_settings().get("bootstrap_on_start", True):
        run_async("full")
