"""Builds API payloads from SQLite + config. Metrics are cached per (db version, cut threshold)."""
from __future__ import annotations

import json
import threading
from datetime import date
from typing import Optional

import pandas as pd

from . import db, metrics
from .config import load_seed, load_settings, load_universe
from .scoring import resolve_stance, score_funds, video_scores

_cache: dict = {"key": None, "metrics": {}}
_cache_lock = threading.Lock()


def all_metrics(universe: dict, cut_threshold: float) -> dict[str, dict]:
    key = (db.version(), round(cut_threshold, 4), len(universe["funds"]))
    with _cache_lock:
        if _cache["key"] == key:
            return _cache["metrics"]
    out = {}
    with db.session() as con:
        for f in universe["funds"]:
            close, divs = db.load_series(con, f["ticker"])
            out[f["ticker"]] = metrics.fund_metrics(close, divs, cut_threshold)
    with _cache_lock:
        _cache.update(key=key, metrics=out)
    return out


def latest_mentions() -> dict[str, dict]:
    with db.session() as con:
        rows = con.execute("SELECT m.ticker, m.video_id, m.date, m.t, m.snippet, m.n, v.title FROM mentions m "
                           "LEFT JOIN videos v ON v.id = m.video_id ORDER BY m.date DESC").fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        if r["ticker"] not in out:
            out[r["ticker"]] = {"video_id": r["video_id"], "title": r["title"], "date": r["date"], "t": r["t"],
                                "snippet": r["snippet"], "url": video_url(r["video_id"], r["t"])}
    return out


def video_url(video_id: str, t: Optional[int] = None) -> str:
    return f"https://youtu.be/{video_id}" + (f"?t={int(t)}" if t else "")


def _stance_payload(f: dict, half_life: float) -> Optional[dict]:
    st = resolve_stance(f.get("stances", []), half_life)
    if not st:
        return None
    for s in [st] + st["history"]:
        s["url"] = s.get("url") or (video_url(s["video_id"]) if s.get("video_id") else None)
    return st


def funds_payload(settings: dict, overrides: Optional[dict] = None, include_benchmarks: bool = False) -> dict:
    s = {**settings, **(overrides or {})}
    uni = load_universe()
    ms = all_metrics(uni, s["cut_threshold"])
    mentions = latest_mentions()
    rows = []
    for f in uni["funds"]:
        m = ms[f["ticker"]]
        rows.append({"ticker": f["ticker"], "name": f.get("name"), "listing": f.get("listing"), "category": f.get("category"),
                     "group": f.get("group"), "subsector": f.get("subsector"),
                     "role": f.get("role", "fund"), "benchmark": f.get("benchmark"), "pair": f.get("pair"),
                     "leverage": f.get("leverage"), "metrics": m,
                     "stance": _stance_payload(f, s["half_life_days"]), "latest_mention": mentions.get(f["ticker"].split(".")[0].split("-")[0]) or mentions.get(f["ticker"])})
    fund_rows = [r for r in rows if r["role"] == "fund"]
    newest = max((r["metrics"]["as_of"] for r in rows if r["metrics"]["as_of"]), default=None)
    scorable = []
    for r in fund_rows:
        a = r["metrics"]["as_of"]
        r["stale"] = bool(a and newest and (pd.Timestamp(newest) - pd.Timestamp(a)).days > s["stale_days"])
        r["no_data"] = a is None
        if not r["stale"] and not r["no_data"]:
            scorable.append(r)
    score_funds(scorable, s["weights"], s["stability_weights"], s["window"], s["scope"], s["missing_return"])
    for r in rows:
        r.setdefault("rank", None)
        r.setdefault("score", None)
        r.setdefault("stale", False)
        r.setdefault("no_data", r["metrics"]["as_of"] is None)
    by_t = {r["ticker"]: r for r in rows}
    for r in fund_rows:
        b = by_t.get(r["benchmark"]) if r["benchmark"] else None
        r["benchmark_tr"] = None
        r["vs_benchmark"] = {}
        if b:
            bm = b["metrics"]
            r["benchmark_tr"] = {**{w: (bm["tr"] or {}).get(w) for w in metrics.WINDOWS}, "inception": bm.get("tr_inception")}
            for w in metrics.WINDOWS:
                a, c = r["metrics"]["tr"].get(w), bm["tr"].get(w)
                r["vs_benchmark"][w] = None if a is None or c is None else a - c
    out_rows = fund_rows + ([r for r in rows if r["role"] != "fund"] if include_benchmarks else [])
    return {"settings": {k: s[k] for k in ("weights", "stability_weights", "window", "scope", "half_life_days")},
            "rows": out_rows, "as_of": max((r["metrics"]["as_of"] for r in rows if r["metrics"]["as_of"]), default=None),
            "n_with_data": sum(1 for r in fund_rows if r["metrics"]["as_of"]), "n_total": len(fund_rows)}


def distributions(ticker: str, years: float = 5.0, cut_threshold: float = 0.10) -> list[dict]:
    with db.session() as con:
        _, divs = db.load_series(con, ticker)
    divs = metrics.clean_divs(divs)
    if len(divs) == 0:
        return []
    start = divs.index[-1] - pd.Timedelta(days=int(365 * years))
    out, prev = [], None
    for ix, v in divs.items():
        if ix >= start:
            ch = None if prev is None else v / prev - 1.0
            out.append({"d": ix.strftime("%Y-%m-%d"), "amount": float(v), "change_pct": ch, "cut": bool(ch is not None and ch < -cut_threshold)})
        prev = v
    return out


def history(ticker: str, years: float = 3.0) -> list[dict]:
    with db.session() as con:
        close, divs = db.load_series(con, ticker)
    close = metrics.clean_close(close)
    if len(close) == 0:
        return []
    idx = metrics.tr_index(close, divs)
    start = close.index[-1] - pd.Timedelta(days=int(365 * years))
    sel = close.index >= start
    base_tr, base_px = idx[sel].iloc[0], close[sel].iloc[0]
    return [{"d": d.strftime("%Y-%m-%d"), "price": float(close[d] / base_px * 100), "total_return": float(idx[d] / base_tr * 100)}
            for d in close.index[sel]]


def videos_payload(settings: dict, half_life: Optional[float] = None, mode: Optional[str] = None, limit: int = 0) -> list[dict]:
    with db.session() as con:
        rows = [dict(r) for r in con.execute("SELECT id,title,date,est_date,views,duration,has_transcript,tickers FROM videos")]
    for r in rows:
        r["tickers"] = json.loads(r["tickers"] or "{}")
        r["url"] = video_url(r["id"])
    sc = video_scores(rows, half_life or settings["half_life_days"], mode or settings["views_transform"])
    return sc[:limit] if limit else sc


def recent_mentions(ticker: str, limit: int = 6) -> list[dict]:
    base = ticker.split(".")[0].split("-")[0]
    with db.session() as con:
        rows = con.execute("SELECT m.ticker, m.video_id, m.date, m.t, m.snippet, v.title FROM mentions m LEFT JOIN videos v ON v.id=m.video_id "
                           "WHERE m.ticker IN (?,?) ORDER BY m.date DESC LIMIT ?", (ticker, base, limit)).fetchall()
    return [{**dict(r), "url": video_url(r["video_id"], r["t"])} for r in rows]
