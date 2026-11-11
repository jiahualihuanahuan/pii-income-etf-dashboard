"""ETF holdings: SQLite tables, bundle import and the look-through engine.

Tables (created by ``init_schema``):
* ``etf_fund``     one row per fund we hold a portfolio for (dashboard ETFs and every fund they hold, recursively)
* ``etf_holding``  direct holdings, weight = % of NAV (negative cash = borrowing); kind: security | fund | cash | derivative | memo
* ``etf_alias``    dashboard symbol -> fund_key
* ``etf_meta``     key/value (import time, bundle info)

``lookthrough`` multiplies weights through sub-funds (depth <= MAX_DEPTH, cycle-safe) down to individual securities.
Weights stay relative to the NAV of the ETF you look at; leverage is already in them (negative cash) or noted per fund.
"""
from __future__ import annotations

import gzip
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from . import db

MAX_DEPTH = 6
STALE_DAYS = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_fund (
  fund_key TEXT PRIMARY KEY, market TEXT, ticker TEXT, name TEXT, issuer TEXT, source TEXT, url TEXT, as_of TEXT,
  n_rows INTEGER, raw_sum REAL, coverage TEXT, partial INTEGER DEFAULT 0, disclosed_pct REAL, leverage REAL, leverage_basis TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS etf_holding (
  fund_key TEXT NOT NULL, pos INTEGER NOT NULL, name TEXT, ticker TEXT, isin TEXT, cusip TEXT, weight REAL, sector TEXT, country TEXT,
  asset_class TEXT, currency TEXT, kind TEXT, sub_fund_key TEXT, unlisted_fund TEXT, PRIMARY KEY (fund_key, pos));
CREATE TABLE IF NOT EXISTS etf_alias (symbol TEXT PRIMARY KEY, fund_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS etf_meta (k TEXT PRIMARY KEY, v TEXT);
"""


def init_schema(con) -> None:
    con.executescript(SCHEMA)


def load_bundle(path: Path) -> dict:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        b = json.load(fh)
    if b.get("format") != 1:
        raise ValueError(f"unsupported bundle format {b.get('format')!r}")
    return b


def import_bundle(bundle: dict, con=None) -> dict:
    """Replace all holdings tables with the bundle contents (one transaction). Returns counts."""
    own = con is None
    ctx = db.session() if own else None
    con = ctx.__enter__() if own else con
    try:
        init_schema(con)
        con.execute("DELETE FROM etf_holding")
        con.execute("DELETE FROM etf_fund")
        con.execute("DELETE FROM etf_alias")
        nh = 0
        for k, f in bundle["funds"].items():
            con.execute("INSERT INTO etf_fund VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (k, f["market"], f["ticker"], f.get("name") or "", f.get("issuer"), f.get("source"), f.get("url"), f.get("as_of"), f.get("n_rows"),
                         f.get("raw_sum"), f.get("coverage"), int(bool(f.get("partial"))), f.get("disclosed_pct"), f.get("leverage"), f.get("leverage_basis"),
                         f.get("note")))
            con.executemany("INSERT INTO etf_holding VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [(k, i, h["name"], h.get("ticker"), h.get("isin"), h.get("cusip"), h["w"], h.get("sector"), h.get("country"), h.get("asset_class"),
                              h.get("currency"), h["kind"], h.get("sub"), h.get("unlisted_fund")) for i, h in enumerate(f["holdings"])])
            nh += len(f["holdings"])
        con.executemany("INSERT INTO etf_alias VALUES (?,?)", list(bundle.get("aliases", {}).items()))
        con.execute("INSERT OR REPLACE INTO etf_meta VALUES ('imported_at', ?)", (datetime.now().isoformat(timespec="seconds"),))
        con.execute("INSERT OR REPLACE INTO etf_meta VALUES ('bundle_generated', ?)", (bundle.get("generated") or "",))
        con.execute("INSERT OR REPLACE INTO etf_meta VALUES ('xray_built', ?)", (bundle.get("xray_built") or "",))
        con.execute("INSERT OR REPLACE INTO etf_meta VALUES ('source_note', ?)", (bundle.get("source_note") or "",))
        if own:
            ctx.__exit__(None, None, None)
    except Exception:
        if own:
            ctx.__exit__(*__import__("sys").exc_info())
        raise
    db.bump()
    _CACHE.clear()
    return {"funds": len(bundle["funds"]), "holdings": nh, "aliases": len(bundle.get("aliases", {}))}


def has_data(con) -> bool:
    init_schema(con)
    return con.execute("SELECT COUNT(*) c FROM etf_fund").fetchone()["c"] > 0


_CACHE: dict = {}


def _fund(con, key: str):
    return con.execute("SELECT * FROM etf_fund WHERE fund_key=?", (key,)).fetchone()


def _rows(con, key: str):
    return con.execute("SELECT * FROM etf_holding WHERE fund_key=? ORDER BY pos", (key,)).fetchall()


def resolve_symbol(con, symbol: str) -> Optional[str]:
    r = con.execute("SELECT fund_key FROM etf_alias WHERE UPPER(symbol)=UPPER(?)", (symbol,)).fetchone()
    return r["fund_key"] if r else None


def sec_key(h) -> str:
    """Identity of a security across funds: CUSIP/ISIN, else ticker, else normalised name."""
    cusip, isin = (h["cusip"] or "").strip().upper(), (h["isin"] or "").strip().upper()
    if len(cusip) == 9:
        return "C:" + cusip
    if len(isin) == 12:
        return "C:" + isin[2:11] if isin[:2] in ("US", "CA") else "I:" + isin
    if h["ticker"]:
        return "T:" + h["ticker"].upper()
    return "N:" + re.sub(r"[^A-Z0-9]", "", (h["name"] or "").upper())


def direct(con, key: str) -> list[dict]:
    out = []
    for h in _rows(con, key):
        sub = h["sub_fund_key"]
        out.append({"name": h["name"], "ticker": h["ticker"] or None, "weight": h["weight"], "sector": h["sector"] or None, "country": h["country"] or None,
                    "asset_class": h["asset_class"] or None, "currency": h["currency"] or None, "kind": h["kind"],
                    "fund_key": sub, "lookthrough": bool(sub and _fund(con, sub)), "unlisted_fund": h["unlisted_fund"]})
    return out


def lookthrough(con, key: str, max_depth: int = MAX_DEPTH) -> dict:
    """Expand sub-funds recursively. Returns aggregated rows + summary buckets (all in % of the top fund's NAV)."""
    agg: dict[str, dict] = {}
    buckets = defaultdict(float)  # security | cash | derivative | opaque_fund | undisclosed | memo(not counted)
    used: dict[str, dict] = {}
    notes: list[dict] = []
    top = _fund(con, key)

    def add(k, h, w, via, kind):
        a = agg.get(k)
        if a is None:
            a = agg[k] = {"name": h["name"], "ticker": h["ticker"] or None, "sector": h["sector"] or None, "country": h["country"] or None,
                          "asset_class": h["asset_class"] or None, "kind": kind, "weight": 0.0, "direct": 0.0, "via": defaultdict(float)}
        else:
            for f in ("sector", "country", "asset_class"):
                if not a[f] and h[f]:
                    a[f] = h[f]
            if not a["ticker"] and h["ticker"]:
                a["ticker"] = h["ticker"]
        a["weight"] += w
        a["via"][via] += w

    def expand(fk, mult, depth, path):
        f = _fund(con, fk)
        u = used.setdefault(fk, {"fund_key": fk, "ticker": f["ticker"], "name": f["name"], "source": f["source"], "as_of": f["as_of"], "url": f["url"],
                                 "partial": bool(f["partial"]), "leverage": f["leverage"], "leverage_basis": f["leverage_basis"], "exposure_pct": 0.0,
                                 "coverage": f["coverage"], "note": f["note"], "depth": depth})
        u["exposure_pct"] += mult
        label = f["ticker"]
        for h in _rows(con, fk):
            w = h["weight"] * mult / 100.0
            kind = h["kind"]
            if kind == "memo":
                continue
            sub = h["sub_fund_key"]
            if kind == "fund" and sub and sub not in path and depth < max_depth and _fund(con, sub):
                expand(sub, w, depth + 1, path + (sub,))
                continue
            if kind == "fund" and sub and sub in path:
                notes.append({"level": "warn", "code": "cycle", "msg": f"{label} holds {sub}, which is already in the chain; not expanded again"})
            if kind == "fund":
                buckets["opaque_fund"] += w
                add("F:" + (sub or h["unlisted_fund"] or h["name"]), h, w, label, "fund")
            elif kind == "cash":
                buckets["cash"] += w
                add("CASH", {**dict(h), "name": "Cash & other assets/liabilities", "ticker": None}, w, label, "cash")
            elif kind == "derivative":
                buckets["derivative"] += w
                add("D:" + sec_key(h) + h["name"], h, w, label, "derivative")
            else:
                buckets["security"] += w
                add(sec_key(h), h, w, label, "security")
        if f["partial"] or f["disclosed_pct"] is not None and f["disclosed_pct"] < 99.5:
            rem = mult * (100.0 - (f["disclosed_pct"] or 0.0)) / 100.0
            if rem > 1e-9:  # (a "partial" file that already sums above 100% has no unknown remainder)
                buckets["undisclosed"] += rem
                add("U:" + fk, {"name": f"Undisclosed remainder of {label} (not in source data)", "ticker": None, "sector": None, "country": None, "asset_class": None},
                    rem, label, "undisclosed")

    expand(key, 100.0, 0, (key,))
    rows = []
    for a in agg.values():
        a["via"] = {k: round(v, 4) for k, v in sorted(a["via"].items(), key=lambda kv: -abs(kv[1]))}
        a["weight"] = round(a["weight"], 6)
        rows.append(a)
    _ORDER = {"security": 0, "fund": 0, "derivative": 1, "cash": 1, "undisclosed": 2}
    rows.sort(key=lambda r: (_ORDER.get(r["kind"], 1), -abs(r["weight"]), r["name"] or ""))
    pos = sum(r["weight"] for r in rows if r["weight"] > 0)
    net = sum(r["weight"] for r in rows)
    return {"rows": rows, "buckets": {k: round(v, 4) for k, v in buckets.items()}, "funds_used": sorted(used.values(), key=lambda u: (u["depth"], -u["exposure_pct"])),
            "notes": notes, "gross_pct": round(pos, 4), "net_pct": round(net, 4), "top": dict(top) if top else None}


OPT_RX = re.compile(r"covered call|call[- ]writing|option|premium|yield|income|enhanced|ultrayield|highpay|maximizer|daymax|0dte|buywrite|target", re.I)


def _flags(con, key: str, lt: dict, dash_name: str = "") -> list[dict]:
    f = _fund(con, key)
    b = lt["buckets"]
    fl: list[dict] = []
    add = lambda level, code, msg: fl.append({"level": level, "code": code, "msg": msg})  # noqa: E731
    and_used = lt["funds_used"]
    seen_notes = set()
    for n in lt["notes"]:
        if n["msg"] not in seen_notes:
            seen_notes.add(n["msg"])
            fl.append(n)
    if b.get("undisclosed", 0) > 0.5:
        parts = [u for u in and_used if u["partial"]]
        add("warn", "undisclosed", f"{b['undisclosed']:.1f}% of the look-through weight is not disclosed by the source "
            f"(top-N / partial files: {', '.join(u['ticker'] for u in parts) or 'n/a'}).")
    if b.get("opaque_fund", 0) > 0.5:
        names = [r["name"] for r in lt["rows"] if r["kind"] == "fund"][:6]
        add("warn", "opaque_fund", f"{b['opaque_fund']:.1f}% sits in funds we have no holdings for, so it is not looked through: {'; '.join(names)}.")
    if b.get("derivative", 0) or any(abs(r["weight"]) > 0 for r in lt["rows"] if r["kind"] == "derivative"):
        add("info", "derivatives", f"Derivative rows (options/swaps/futures) are listed separately: net {b.get('derivative', 0):.2f}% of NAV.")
    elif OPT_RX.search((dash_name or "") + " " + (f["name"] or "")) and not b.get("derivative"):
        add("info", "options_not_shown", "Option overlay: written calls/puts are generally not in the issuer holdings file, so their exposure is not shown.")
    if b.get("cash", 0) < -0.5:
        add("info", "borrowing", f"Cash is {b['cash']:.1f}% (negative = borrowing/leverage); weights are % of NAV.")
    lev_nodes = [u for u in and_used if u["leverage"]]
    if f["leverage"]:
        basis = "derived from the holdings (positive weights / NAV)" if f["leverage_basis"] == "holdings" else "from the issuer's 'Enhanced' naming convention, not stated in the data"
        add("info", "leverage", f"Leverage ~{f['leverage']:.2f}x ({basis}).")
    elif lev_nodes:
        add("info", "leverage_underlying", "Leveraged underlying funds: " + ", ".join(f"{u['ticker']} ~{u['leverage']:.2f}x" for u in lev_nodes[:8]) + ".")
    proxies = [u for u in and_used if (u["source"] or "").upper().startswith("PROXY") and u["exposure_pct"] >= 0.5]
    for u in proxies:
        add("warn", "proxy", f"{u['ticker']} ({u['exposure_pct']:.1f}% of this ETF) is a PROXY, not the fund's own full holdings. {u.get('note') or u['source']}")
    material = [u for u in and_used if u["exposure_pct"] >= 5.0 or u["fund_key"] == key]  # tiny sleeves do not drive the flags below
    for u in material:
        if (u["source"] or "").startswith("SEC"):
            add("info", "nport", f"{u['ticker']}: SEC N-PORT quarter-end snapshot as of {u['as_of']} (not daily).")
            break
    try:
        oldest = min((u for u in material if u["as_of"]), key=lambda u: u["as_of"])
        age = (date.today() - date.fromisoformat(oldest["as_of"])).days
        if age > STALE_DAYS:
            add("warn", "stale", f"Oldest source in the chain is {age} days old ({oldest['ticker']} as of {oldest['as_of']}).")
    except ValueError:
        pass
    if abs(lt["net_pct"] - 100.0) > 1.5 and not b.get("undisclosed"):
        add("info", "weights_not_100", f"Published weights add up to {lt['net_pct']:.1f}% of NAV (leverage or overlapping rows in the source).")
    if f["note"]:
        add("info", "memo_rows", f["note"])
    for u in and_used:
        if u["fund_key"] != key and u["partial"] and u["coverage"]:
            add("info", "partial_source", f"{u['ticker']}: {u['coverage']}.")
            break
    return fl


def summary(con, key: str, lt: dict, dash_name: str = "") -> dict:
    b = lt["buckets"]
    reached = b.get("security", 0.0)
    flags = _flags(con, key, lt, dash_name)
    # "fully looked through" = everything reaches individual securities, cash or derivatives: no undisclosed remainder, no opaque fund, no cycle
    complete = b.get("undisclosed", 0.0) <= 1.0 and b.get("opaque_fund", 0.0) <= 1.0 and not any(x["code"] == "cycle" for x in flags)
    return {"individual_pct": round(reached, 2), "cash_pct": round(b.get("cash", 0.0), 2), "derivative_pct": round(b.get("derivative", 0.0), 2),
            "opaque_fund_pct": round(b.get("opaque_fund", 0.0), 2), "undisclosed_pct": round(b.get("undisclosed", 0.0), 2),
            "gross_pct": lt["gross_pct"], "net_pct": lt["net_pct"], "complete": bool(complete), "n_rows": len(lt["rows"]), "flags": flags}


def etf_payload(con, symbol: str, dash_name: str = "", lookthrough_mode: bool = True, q: str = "", limit: int = 0, offset: int = 0) -> dict:
    key = resolve_symbol(con, symbol)
    if not key:
        return {"ticker": symbol, "available": False, "reason": "No holdings data for this ETF in the imported sources."}
    f = _fund(con, key)
    ck = ("lt", key, db.version())
    lt = _CACHE.get(ck)
    if lt is None:
        lt = _CACHE[ck] = lookthrough(con, key)
    summ = summary(con, key, lt, dash_name)
    if lookthrough_mode:
        rows = [{**r, "weight": r["weight"]} for r in lt["rows"]]
    else:
        rows = direct(con, key)
        rows.sort(key=lambda r: (r["kind"] == "memo", -abs(r["weight"])))
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r["name"] or "").lower() or ql in (r["ticker"] or "").lower() or ql in (r.get("sector") or "").lower()]
    total = len(rows)
    if offset:
        rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    return {"ticker": symbol, "available": True, "fund_key": key, "fund_ticker": f["ticker"], "name": f["name"] or dash_name, "source": f["source"], "url": f["url"],
            "as_of": f["as_of"], "coverage": f["coverage"], "partial": bool(f["partial"]), "leverage": f["leverage"], "leverage_basis": f["leverage_basis"],
            "mode": "lookthrough" if lookthrough_mode else "direct", "n_direct": f["n_rows"], "n_total": total, "summary": summ, "funds_used": lt["funds_used"],
            "rows": rows}


def coverage(con, universe: list[dict]) -> dict:
    """Per dashboard fund: has data? looked through fully? Used by /api/holdings/coverage and the report."""
    ck = ("cov", db.version())
    if ck in _CACHE:
        return _CACHE[ck]
    items = []
    for f in universe:
        key = resolve_symbol(con, f["ticker"])
        if not key:
            items.append({"ticker": f["ticker"], "role": f.get("role", "fund"), "has_holdings": False})
            continue
        fu = _fund(con, key)
        lt = lookthrough(con, key)
        s = summary(con, key, lt, f.get("name") or "")
        items.append({"ticker": f["ticker"], "role": f.get("role", "fund"), "has_holdings": True, "fund_key": key, "source": fu["source"], "as_of": fu["as_of"],
                      "individual_pct": s["individual_pct"], "undisclosed_pct": s["undisclosed_pct"], "opaque_fund_pct": s["opaque_fund_pct"],
                      "complete": s["complete"], "flags": [x["code"] for x in s["flags"]]})
    funds = [i for i in items if i["role"] == "fund"]
    out = {"total_funds": len(funds), "with_holdings": sum(1 for i in funds if i["has_holdings"]),
           "fully_looked_through": sum(1 for i in funds if i["has_holdings"] and i["complete"]),
           "missing": [i["ticker"] for i in funds if not i["has_holdings"]], "items": items}
    _CACHE[ck] = out
    return out
