"""FastAPI app."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import db, jobs, service
from .config import (UI_EDITABLE, load_seed, load_settings, load_universe, reset_override, save_override,
                     validate_settings)
from .scoring import DEFAULT_STABILITY_WEIGHTS, DEFAULT_WEIGHTS, TR_WINDOWS

log = logging.getLogger("app")
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    jobs.bootstrap()
    s = load_settings()
    if s.get("scheduler_enabled", True):
        _scheduler = jobs.build_scheduler(s)
        _scheduler.start()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Income ETF Dashboard", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


class SettingsUpdate(BaseModel):
    weights: Optional[dict[str, float]] = None
    stability_weights: Optional[dict[str, float]] = None
    window: Optional[str] = None
    scope: Optional[str] = None
    half_life_days: Optional[float] = Field(None, gt=0, le=3650)
    views_transform: Optional[str] = None
    cut_threshold: Optional[float] = Field(None, ge=0, le=0.9)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    uni = load_universe()
    with db.session() as con:
        priced = db.count_prices(con)
        nvid = con.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"]
        errs = [r["ticker"] for r in con.execute("SELECT ticker FROM fund_meta WHERE error IS NOT NULL")]
    scheduled = []
    if _scheduler:
        scheduled = [{"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None} for j in _scheduler.get_jobs()]
    return {"funds": len(uni["funds"]), "funds_with_prices": priced, "videos": nvid, "tickers_without_data": errs,
            "jobs": db.recent_jobs(10), "scheduled": scheduled}


@app.get("/api/settings")
def get_settings():
    return {"settings": load_settings(), "defaults": {"weights": DEFAULT_WEIGHTS, "stability_weights": DEFAULT_STABILITY_WEIGHTS},
            "windows": TR_WINDOWS, "editable": list(UI_EDITABLE)}


@app.put("/api/settings")
def put_settings(body: SettingsUpdate):
    upd = body.model_dump(exclude_none=True)
    if "window" in upd and upd["window"] not in TR_WINDOWS:
        raise HTTPException(422, f"window must be one of {TR_WINDOWS}")
    if "scope" in upd and upd["scope"] not in ("all", "category"):
        raise HTTPException(422, "scope must be 'all' or 'category'")
    if "views_transform" in upd and upd["views_transform"] not in ("log", "linear", "none"):
        raise HTTPException(422, "views_transform must be log, linear or none")
    for k in ("weights", "stability_weights"):
        if k in upd and any(v < 0 for v in upd[k].values()):
            raise HTTPException(422, f"{k} must be non-negative")
    return {"settings": save_override(upd)}


@app.delete("/api/settings")
def delete_settings():
    return {"settings": reset_override()}


@app.get("/api/funds")
def funds(window: Optional[str] = None, scope: Optional[str] = None, half_life_days: Optional[float] = Query(None, gt=0),
          w_total_return: Optional[float] = Query(None, ge=0), w_stability: Optional[float] = Query(None, ge=0),
          w_yield: Optional[float] = Query(None, ge=0), category: Optional[str] = None, listing: Optional[str] = None,
          benchmarks: bool = False):
    """Ranked funds. Query params override saved settings for this request only (the UI sliders use them)."""
    s = load_settings()
    ov: dict = {}
    if window:
        ov["window"] = window
    if scope:
        ov["scope"] = scope
    if half_life_days:
        ov["half_life_days"] = half_life_days
    w = dict(s["weights"])
    for k, v in (("total_return", w_total_return), ("stability", w_stability), ("yield", w_yield)):
        if v is not None:
            w[k] = v
    ov["weights"] = w
    ov = validate_settings({**s, **ov})
    payload = service.funds_payload(s, {k: ov[k] for k in ("window", "scope", "half_life_days", "weights")}, include_benchmarks=benchmarks)
    rows = payload["rows"]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if listing:
        rows = [r for r in rows if (r["listing"] or "").upper() == listing.upper()]
    payload["rows"] = sorted(rows, key=lambda r: (r["rank"] is None, r["rank"] or 0, r["ticker"]))
    return payload


@app.get("/api/funds/{ticker}")
def fund(ticker: str):
    p = service.funds_payload(load_settings(), include_benchmarks=True)
    for r in p["rows"]:
        if r["ticker"].upper() == ticker.upper():
            f = next(x for x in load_universe()["funds"] if x["ticker"] == r["ticker"])
            return {**r, "notes": f.get("notes"), "strategy": f.get("strategy"), "aliases": f.get("aliases"),
                    "mentions": [m for m in service.recent_mentions(r["ticker"])]}
    raise HTTPException(404, "unknown ticker")


@app.get("/api/funds/{ticker}/distributions")
def fund_distributions(ticker: str, years: float = Query(5, gt=0, le=40)):
    return {"ticker": ticker, "items": service.distributions(ticker, years, load_settings()["cut_threshold"])}


@app.get("/api/funds/{ticker}/history")
def fund_history(ticker: str, years: float = Query(3, gt=0, le=40)):
    return {"ticker": ticker, "items": service.history(ticker, years)}


@app.get("/api/categories")
def categories():
    uni = load_universe()
    counts: dict[str, int] = {}
    for f in uni["funds"]:
        if f.get("role", "fund") == "fund":
            counts[f.get("category")] = counts.get(f.get("category"), 0) + 1
    return [{"key": k, **v, "count": counts.get(k, 0)} for k, v in uni["categories"].items()]


@app.get("/api/stances")
def stances(half_life_days: Optional[float] = Query(None, gt=0)):
    s = load_settings()
    hl = half_life_days or s["half_life_days"]
    out = []
    for f in load_universe()["funds"]:
        st = service._stance_payload(f, hl)
        if st:
            out.append({"ticker": f["ticker"], "name": f.get("name"), "category": f.get("category"), "stance": st})
    out.sort(key=lambda x: x["stance"]["date"], reverse=True)
    return out


@app.get("/api/videos")
def videos(half_life_days: Optional[float] = Query(None, gt=0), views_transform: Optional[str] = None, limit: int = Query(0, ge=0)):
    return service.videos_payload(load_settings(), half_life_days, views_transform, limit)


@app.get("/api/mindset")
def mindset():
    return load_seed("mindset.json") or {}


@app.post("/api/jobs/{name}/run")
def run_job(name: str, bg: BackgroundTasks):
    if name not in ("full", "prices", "dividends", "videos"):
        raise HTTPException(404, "unknown job")
    bg.add_task(jobs.run_job, name)
    return {"started": name}
