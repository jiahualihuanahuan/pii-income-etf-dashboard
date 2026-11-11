"""Build a compact holdings bundle for the dashboard universe from the portfolio-xray index.

The xray index (``portfolio-xray/data``: ``funds.csv``, ``universe.csv``, ``holdings_CA.csv.gz``, ``holdings_US.csv.gz``) is the merged
output of the monthly issuer scrapes / SEC N-PORT pulls (see its README). Nothing is scraped here: this module only selects the funds
of ``config/tickers.yaml`` (plus every fund they hold, recursively), normalises weights to % of NAV and writes one gzipped JSON file.

    python -m app.holdings_import --from-xray /path/to/portfolio-xray/data --write-bundle /tmp/pii_holdings_bundle.json.gz

Weight rules (the index mixes scales):
* non-partial fund: weights are divided by their own sum, so they add to 100 (this also handles funds stored as fractions).
  Negative cash (Hamilton / leveraged funds) is kept: weights stay relative to NAV and leverage = sum(positive) / sum(all).
* partial fund (top-N only): weights are used as stated (percent; fractions are detected and scaled x100). The rest is "undisclosed".
* Global X "enhanced" files list the sub-fund(s) + cash (~100%) AND the underlying top-10 stocks on top (raw sum 138-200%).
  Those extra stock rows are kept as non-additive ``memo`` rows so nothing is double counted.
* Sub-funds are resolved deterministically only (ISIN, ticker column, "(TICKER)" in the name, exact normalised name). No fuzzy matching.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

csv.field_size_limit(10**8)

FORMAT = 1
MAX_DEPTH = 6
FUNDY = re.compile(r"\b(ETF|ETFS|FUND|INDEX|TRUST|ISHARES|VANGUARD|SPDR|PORTFOLIO|POWERSHARES|YIELD MAXIMIZER|DAYMAX)\b", re.I)
CASH_RX = re.compile(r"^(cash|cash\s*(&|and)\s*cash equivalents?|cash and other assets.*|cash and cash equivalents?|other assets.*|cad cash|usd cash|net other assets.*|us dollar|canadian dollar)$", re.I)
DERIV_RX = re.compile(r"\b(swap|option|options|future|futures|forward|warrant|total return|written call|call option|put option)\b|,\s*(CALL|PUT)\s*,", re.I)
FUND_NAME_RX = re.compile(r"\bETFS?\b|\bMINISHARES\b|\b(ISHARES|SPDR|VANECK|PROSHARES|INVESCO)\b.*\bTRUST\b", re.I)
REIT_RX = re.compile(r"real estate|\bREITS?\b|\bproperty\b|properties", re.I)
BULLION_RX = re.compile(r"\b(gold|silver|platinum|palladium|bitcoin|ether(eum)?)\b.*\b(trust|shares|etf)\b", re.I)
NAME_FIXES = {  # typos in issuer files that stop an exact-name match (explicit, reviewed)
    "BROMPTON GLOBAL INFRASTUCTURE ETF": "BGIE",
    "NINEPOINT ROYAL BANK HIGHSHARES ETF": "RYHI",  # ECHI's file uses the short names; the funds are the "RY-Linked" / "TD-Linked" HighShares ETFs
    "NINEPOINT TD HIGHSHARES ETF": "TDHI",
    "GLOBAL X LITHIUM & BATTERY T": "LIT",  # CMCC's page cuts the name at 28 characters; LIT is the only Global X fund starting "Lithium & Battery T"
    "GLOBAL X EQUAL WEIGHT CANADIAN PIPELINES INDEX ETF": "PPLN",  # the fund's own statements say "Pipelines", the index file "Pipeline"
}
SEC = {"INFORMATION TECHNOLOGY": "Information Technology", "IT": "Information Technology", "TECHNOLOGY": "Information Technology",
       "COMMUNICATION": "Communication Services", "COMMUNICATION SERVICES": "Communication Services", "TELECOMMUNICATION SERVICES": "Communication Services",
       "CONSUMER DISCRETIONARY": "Consumer Discretionary", "CONSUMER STAPLES": "Consumer Staples", "HEALTH CARE": "Health Care", "HEALTHCARE": "Health Care",
       "FINANCIALS": "Financials", "FINANCIAL": "Financials", "INDUSTRIALS": "Industrials", "ENERGY": "Energy", "MATERIALS": "Materials",
       "REAL ESTATE": "Real Estate", "UTILITIES": "Utilities"}
CTY = {"US": "United States", "USA": "United States", "CA": "Canada", "CAN": "Canada", "GB": "United Kingdom", "UK": "United Kingdom", "JP": "Japan",
       "DE": "Germany", "FR": "France", "CH": "Switzerland", "NL": "Netherlands", "AU": "Australia", "CN": "China", "TW": "Taiwan", "KR": "South Korea",
       "IN": "India", "IE": "Ireland", "HK": "Hong Kong", "BR": "Brazil"}


def norm_name(n: str) -> str:
    n = re.sub(r"\([^)]*\)", " ", n or "")
    n = re.sub(r"/(CANADA|TO|CN)\b", " ", n.upper())
    n = re.sub(r"[^A-Z0-9 ]", " ", n.upper())
    n = re.sub(r"\b(INC|CORP|CORPORATION|LTD|LIMITED|PLC|CO|THE|CLASS|CL|COM|SA|AG|NV|HOLDINGS?)\b", " ", n)
    return " ".join(n.split())


def norm_sector(x: str) -> str:
    x = (x or "").strip()
    if not x or x in ("-", "N/A"):
        return ""
    if x.upper() in ("EQUITY", "EQUITIES", "FIXED INCOME", "CASH", "COMMON STOCK", "STOCK", "ETF", "FUND", "UNKNOWN", "OTHER", "CASH AND/OR DERIVATIVES", "DIVERSIFIED") or x.upper().startswith("OPEN END"):
        return ""
    return SEC.get(x.upper(), x.title() if x.isupper() else x)


def norm_country(c: str) -> str:
    c = (c or "").strip()
    if not c or c in ("-", "N/A"):
        return ""
    return CTY.get(c.upper(), c.title() if c.isupper() else c)


def asset_class(ac: str, name: str = "") -> str:
    a = (ac or "").strip()
    u = a.upper()
    if not a:
        return ""
    if u.startswith("EQ") or "EQUIT" in u or u in ("EC", "EP", "COMMON STOCK", "STOCK"):
        return "Equity"
    if u.startswith("FI") or "BOND" in u or "FIXED" in u or u.startswith("DBT") or u == "DEBT":
        return "Fixed income"
    if "CASH" in u or "MONEY" in u or u in ("STIV", "CA"):
        return "Cash"
    if "FUTURE" in u or "DERIV" in u or "SWAP" in u or "OPTION" in u or u in ("DFE", "DIR", "DCO", "DE"):
        return "Derivative"
    if "REAL" in u or "REIT" in u:
        return "Real estate"
    if "COMMOD" in u or "GOLD" in u:
        return "Commodity"
    return a


def sec_id(isin: str, cusip: str) -> Optional[str]:
    isin, cusip = (isin or "").strip().upper(), (cusip or "").strip().upper()
    if len(cusip) == 9 and cusip != "000000000":
        return "C:" + cusip
    if len(isin) == 12:
        return "C:" + isin[2:11] if isin[:2] in ("US", "CA") else "I:" + isin
    return None


class XrayIndex:
    """Reads the xray data dir lazily; keeps only the rows of wanted funds in memory."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        meta = json.loads((self.dir / "meta.json").read_text())
        self.meta = meta
        self.parts = meta.get("holdings_parts", ["holdings.csv.gz"])
        for p in self.parts:
            if not (self.dir / p).exists():
                raise FileNotFoundError(f"missing {self.dir / p}")
        self.funds: dict[str, dict] = {}
        for r in csv.DictReader(open(self.dir / "funds.csv", encoding="utf-8")):
            self.funds[r["m"] + ":" + r["fund"]] = r
        self.univ: dict[tuple, dict] = {}
        self.isin2fund: dict[str, str] = {}
        self.name2fund: dict[str, set] = defaultdict(set)
        for r in csv.DictReader(open(self.dir / "universe.csv", encoding="utf-8")):
            for t in {r["ticker"], r["base"]}:
                if t:
                    self.univ.setdefault((r["m"], t.upper()), r)
            key = r["m"] + ":" + r["ticker"]
            if r["isin"]:
                self.isin2fund.setdefault(r["isin"], key)
            if r["name"]:
                self.name2fund[norm_name(r["name"])].add(key)
        self.sector_by_key: dict = {}
        self.sector_by_name: dict[str, str] = {}
        self.ids_by_name: dict[str, set] = defaultdict(set)  # normalised security name -> {(ticker, isin, cusip)} seen in the index

    def rows_for(self, wanted: set[str]) -> dict[str, list[list[str]]]:
        """One pass over every part; returns rows of the wanted fund keys and learns a sector per security id."""
        out: dict[str, list] = defaultdict(list)
        learn = not self.sector_by_key
        for p in self.parts:
            with gzip.open(self.dir / p, "rt", encoding="utf-8") as fh:
                rd = csv.reader(fh)
                next(rd)
                for r in rd:
                    k = r[0] + ":" + r[1]
                    if k in wanted:
                        out[k].append(r)
                    if learn and len(r) > 10:
                        if r[3] and (r[5] or r[6]) and not FUND_NAME_RX.search(r[3]):
                            self.ids_by_name[norm_name(r[3])].add((r[4].strip() if r[4] not in ("-", "N/A") else "", r[5].strip(), r[6].strip()))
                        sec = norm_sector(r[8])
                        if sec and r[3] and not FUND_NAME_RX.search(r[3]):
                            self.sector_by_name.setdefault(norm_name(r[3]), sec)
                        if sec:
                            sid = sec_id(r[5], r[6]) or ((r[4].upper() + "|" + (r[14] if len(r) > 14 else "")) if r[4] and r[4] not in ("-", "N/A") else None)
                            if sid:
                                self.sector_by_key.setdefault(sid, sec)
        return out

    def has_rows(self, key: str, rows: dict) -> bool:
        return key in self.funds and bool(rows.get(key))

    # ---- deterministic sub-fund resolution ----
    def key_for(self, m: str, t: str) -> Optional[str]:
        """xray fund key (the one in funds.csv) for a ticker in market m, or None. Tries t, t.U, then the universe row's ticker/base."""
        t = t.upper()
        for c in (t, t + ".U", t[:-2] if t.endswith(".U") else t):
            if m + ":" + c in self.funds:
                return m + ":" + c
        u = self.univ.get((m, t))
        if u:
            for c in (u["ticker"], u["base"]):
                if c and m + ":" + c in self.funds:
                    return m + ":" + c
        return None

    def resolve_fund(self, row: list[str], holder_m: str, holder_key: str = "") -> Optional[str]:
        """Sub-fund key for a holding row. Returns a key that may have no rows (caller checks). Never fuzzy."""
        name, tkr, isin, cusip, ac = row[3], (row[4] or "").upper().strip(), (row[5] or "").strip().upper(), (row[6] or "").strip(), row[10]
        if isin and isin in self.isin2fund:
            k = self.isin2fund[isin]
            return self.key_for(*k.split(":", 1)) or k
        if isin or cusip:
            return None  # has a security id that is not a fund we know
        looks = bool(FUND_NAME_RX.search(name)) and not REIT_RX.search(name)
        looks = looks or "ETF" in (ac or "").upper() or "FUND" in (ac or "").upper()
        truncated = len(row[3]) >= 49 and row[3].upper().startswith("GLOBAL X ")  # issuer files cut names at 50 chars
        if not (looks or truncated):
            return None
        cands = []
        if tkr:
            cands.append(tkr)
        m = re.search(r"\(([A-Z0-9.\-]{2,8})\)\s*$", name.upper())
        if m:
            cands.append(m.group(1))
        raw = (row[13] if len(row) > 13 else "").upper().split(" ")[0]
        if raw:
            cands.append(raw)
        order = [holder_m, "US" if holder_m == "CA" else "CA"]
        for t in cands:
            for mk in order:
                k = self.key_for(mk, t)
                if k and k != holder_key:
                    return k
        nn = norm_name(name)
        if nn in NAME_FIXES_N:
            for mk in order:
                k = self.key_for(mk, NAME_FIXES_N[nn])
                if k:
                    return k
        hits = {self.key_for(*h.split(":", 1)) or h for h in self.name2fund.get(nn, set())}
        hits.discard(holder_key)
        hits = {h for h in hits if not (h.endswith(".U") and h[:-2] in hits)}  # share-class twin carries the same portfolio
        if len(hits) != 1:
            pref = {h for h in hits if h.startswith(holder_m + ":")}
            hits = pref if len(pref) == 1 else hits
        if len(hits) == 1:
            return next(iter(hits))
        if truncated and holder_key:  # unique prefix among universe names, same issuer family
            fam = nn.split(" ")[0]
            pre = {self.key_for(*kk.split(":", 1)) or kk for n2, ks in self.name2fund.items() if n2.startswith(nn) and n2.split(" ")[0] == fam for kk in ks}
            pre.discard(holder_key)
            pre = {h for h in pre if not (h.endswith(".U") and h[:-2] in pre)}
            if len(pre) == 1:
                return next(iter(pre))
        return None


NAME_FIXES_N = {norm_name(k): v for k, v in NAME_FIXES.items()}


def dashboard_aliases(symbols: Iterable[str], idx: XrayIndex, extra_keys: Iterable[str] = ()) -> dict[str, str]:
    """dashboard symbol -> xray fund key. US listings match US only; X.TO -> CA:X; X-U.TO -> its CA share-class twin."""
    out: dict[str, str] = {}
    extra = set(extra_keys)
    for s in symbols:
        if s.endswith(".TO"):
            b = s[:-3]
            for c in ([b.replace("-U", ".U"), b[:-2]] if b.endswith("-U") else []) + [b]:
                k = idx.key_for("CA", c) or (("CA:" + c) if ("CA:" + c) in extra else None)  # supplement-only funds (not in the xray index)
                if k:
                    out[s] = k
                    break
        else:
            k = idx.key_for("US", s) or (("US:" + s) if ("US:" + s) in extra else None)
            if k:
                out[s] = k
    return out


def leverage_of(rows_pct: list[tuple[float, bool]], name: str, issuer: str, partial: bool) -> tuple[Optional[float], Optional[str]]:
    """(ratio, basis). From holdings when negative cash/borrowing is listed; else the issuer 'Enhanced' naming convention."""
    net = sum(w for w, _ in rows_pct)
    pos = sum(w for w, _ in rows_pct if w > 0)
    if not partial and net > 0 and pos / net > 1.03:
        return round(pos / net, 2), "holdings"
    if re.search(r"^(hamilton|global x|harvest)\b.*\benhanced\b", name, re.I) and not re.search(r"enhanced (yield|premium|equity income fund)", name, re.I):
        return 1.25, "issuer_convention"
    return None, None


def build_fund(idx: XrayIndex, key: str, rows: list[list[str]]) -> dict:
    info = idx.funds[key]
    m = key.split(":")[0]
    partial = info["partial"] == "1"
    try:
        rawsum = float(info["rawsum"] or 0)
    except ValueError:
        rawsum = 0.0
    items = []
    for r in rows:
        try:
            w = float(r[7])
        except ValueError:
            continue
        name, tkr = r[3].strip(), (r[4] or "").strip()
        sub = idx.resolve_fund(r, m, key)
        ac = asset_class(r[10], name)
        has_id = bool(r[5] or r[6])
        is_cash = ac == "Cash" or bool(CASH_RX.match(name)) or (name.upper().endswith(" CASH") and tkr.upper() in ("CAD", "USD", "EUR", "GBP", "JPY"))
        is_deriv = ac == "Derivative" or (not sub and bool(DERIV_RX.search(name)) and not has_id)
        looks_fund = bool(FUND_NAME_RX.search(name)) and not has_id and not REIT_RX.search(name)
        kind = "cash" if is_cash else "derivative" if is_deriv else "fund" if (sub or looks_fund) else "security"
        sid = sec_id(r[5], r[6]) or ((tkr.upper() + "|" + (r[14] if len(r) > 14 else "")) if tkr and tkr not in ("-", "N/A") else None)
        sector = norm_sector(r[8]) or (idx.sector_by_key.get(sid, "") if sid and kind == "security" else "")
        items.append({"name": name, "ticker": ("" if tkr in ("-", "N/A") else tkr), "isin": (r[5] or "").strip(), "cusip": (r[6] or "").strip(),
                      "w": w, "sector": sector, "country": norm_country(r[9]), "asset_class": ac, "currency": (r[11] or "").strip(),
                      "kind": kind, "sub": sub})
    # Files that list the sub-fund(s) + cash (~100%) AND then the underlying's top holdings (Global X / Hamilton DayMAX) -> memo rows
    memo_note = None
    if rawsum > 105 and items:
        cum, cut = 0.0, None
        for i, it in enumerate(items):
            if it["kind"] not in ("fund", "cash"):
                break
            cum += it["w"]
            if 97 <= cum <= 103 and any(x["kind"] == "fund" for x in items[: i + 1]):
                cut = i + 1
        if cut is None and partial:  # top-10 pages: second block repeats securities already listed -> everything from the first repeat is memo
            seen = set()
            for i, it in enumerate(items):
                k = it["name"].upper()
                if it["kind"] == "security" and k in seen:
                    cut = i
                    break
                seen.add(k)
        if cut is not None and cut < len(items):
            for it in items[cut:]:
                it["kind"] = "memo"
            memo_note = ("The issuer file also lists the top holdings of the underlying fund(s) (raw sum %.0f%%). Those rows are shown as memo, "
                         "not added to the weights, so nothing is counted twice; the underlying is expanded by look-through." % rawsum)
    add = [it for it in items if it["kind"] != "memo"]
    tot = sum(it["w"] for it in add)
    if partial:
        scale = 100.0 if 0 < tot <= 1.5 and rawsum <= 1.5 else 1.0
    else:
        scale = 100.0 / tot if tot else 1.0
    for it in items:
        it["w"] = round(it["w"] * scale, 6)
    disclosed = sum(it["w"] for it in add)
    lev, basis = leverage_of([(it["w"], True) for it in add], info.get("name") or "", info["issuer"], partial)
    return {"key": key, "market": m, "ticker": key.split(":", 1)[1], "issuer": info["issuer"], "source": info["source"], "url": info["url"],
            "as_of": info["asof"], "n_rows": len(items), "raw_sum": rawsum, "coverage": info["coverage"], "partial": partial,
            "scale": round(scale, 6), "disclosed_pct": round(disclosed, 4), "leverage": lev, "leverage_basis": basis, "note": memo_note,
            "holdings": items}


def name_keys(name: str) -> list[str]:
    """Normalised-name keys to look a reported security name up in the index (also without a trailing share class / ADR)."""
    n = norm_name(re.sub(r",?\s*(ADR|GDR|Sponsored ADR)\b", "", name or "", flags=re.I))
    out = [n]
    n2 = re.sub(r"\s+[A-Z]$", "", n)
    if n2 != n:
        out.append(n2)
    return out


def load_supplements(sdir: Optional[Path]) -> dict[str, dict]:
    """Fund holdings taken from fund financial statements / proxies: config/holdings_supplements/*.json ({"funds": {fund_key: entry}})."""
    out: dict[str, dict] = {}
    if not sdir or not Path(sdir).is_dir():
        return out
    for f in sorted(Path(sdir).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for k, e in (d.get("funds") or {}).items():
            e["_file"] = f.name
            out[k] = e
    return out


def make_tail_proxy(idx: XrayIndex, key: str, e: dict, rows: dict[str, list]) -> dict:
    """Top-N fund whose full list is not published: keep the published top-N rows, fill the rest from a sister fund tracking the same index.

    The filler rows are the proxy fund's securities that are not already in the published list, rescaled to fill the undisclosed rest of NAV.
    ``name_aliases`` maps a published name to the proxy fund's spelling (explicit, reviewed) so nothing is counted twice.
    """
    base = build_fund(idx, key, rows[key])
    prox = build_fund(idx, e["proxy_of"], rows[e["proxy_of"]])
    alias = {norm_name(a): norm_name(b) for a, b in (e.get("name_aliases") or {}).items()}
    have = {alias.get(norm_name(h["name"]), norm_name(h["name"])) for h in base["holdings"] if h["kind"] == "security"}
    have |= {sec_id(h["isin"], h["cusip"]) for h in base["holdings"] if sec_id(h["isin"], h["cusip"])}
    tail = [h for h in prox["holdings"] if h["kind"] == "security" and h["asset_class"] == "Equity" and h["w"] > 0
            and norm_name(h["name"]) not in have and (sec_id(h["isin"], h["cusip"]) or "") not in have]
    rest = 100.0 - sum(h["w"] for h in base["holdings"] if h["kind"] != "memo")
    tsum = sum(h["w"] for h in tail)
    if rest <= 0 or tsum <= 0:
        raise ValueError(f"tail proxy {key}: nothing to fill (rest={rest}, tail={tsum})")
    k = rest / tsum
    hold = [h for h in base["holdings"] if h["kind"] != "memo"] + [dict(h, w=round(h["w"] * k, 6), sub=None, proxy_tail=True) for h in tail]
    note = (f"{len(base['holdings'])} positions are {key.split(':', 1)[1]}'s own published weights (as of {base['as_of']}); the other {rest:.1f}% is ESTIMATED from the "
            f"{len(tail)} constituents of {e['proxy_of'].split(':', 1)[1]} ({prox['as_of']}) that are not in that list, rescaled x{k:.2f} to fill it. Not actual holdings.")
    return {"name": e.get("name") or "", "issuer": base["issuer"], "source": e["source"], "url": base["url"], "as_of": base["as_of"], "proxy": True, "note": note,
            "coverage": f"PROXY for the undisclosed {rest:.1f}%: {e['proxy_of']} constituents", "holdings": hold, "_file": "(tail proxy)"}


def _pseudo_row(m: str, key: str, h: dict) -> list[str]:
    return [m, key.split(":", 1)[1], "D", h["name"], h.get("ticker") or "", h.get("isin") or "", h.get("cusip") or "", str(h["w"]), h.get("sector") or "",
            h.get("country") or "", h.get("asset_class") or "", h.get("currency") or "", "", "", ""]


def supp_subs(idx: XrayIndex, key: str, entry: dict) -> dict[int, str]:
    """position -> sub-fund key for the fund rows of a supplement."""
    m = key.split(":")[0]
    out = {}
    for i, h in enumerate(entry["holdings"]):
        if h.get("kind") == "fund":
            sub = idx.resolve_fund(_pseudo_row(m, key, h), m, key)
            if sub and sub != key:
                out[i] = sub
    return out


def build_supp_fund(idx: XrayIndex, key: str, entry: dict) -> dict:
    """A bundle fund from a supplement entry (weights are % of NAV and kept as reported; the entry says whether it is actual or a proxy)."""
    m = key.split(":")[0]
    subs = supp_subs(idx, key, entry)
    items = []
    for i, h in enumerate(entry["holdings"]):
        it = {"name": h["name"], "ticker": h.get("ticker") or "", "isin": h.get("isin") or "", "cusip": h.get("cusip") or "", "w": float(h["w"]),
              "sector": h.get("sector") or "", "country": h.get("country") or "", "asset_class": h.get("asset_class") or "", "currency": h.get("currency") or "",
              "kind": h.get("kind") or "security", "sub": subs.get(i)}
        if it["kind"] == "fund" and not it["sub"] and not BULLION_RX.search(it["name"]):
            pass  # stays an opaque fund (flagged); never guessed
        if it["kind"] == "security" and not (it["ticker"] or it["isin"] or it["cusip"]):
            seen = set()
            for nk in name_keys(it["name"]):
                seen = idx.ids_by_name.get(nk) or set()
                if seen:
                    break
            if len(seen) >= 1:
                for pick in ((lambda v: v[2]), (lambda v: v[1]), (lambda v: v[0])):  # all candidates must agree on the id used
                    vals = {pick(v) for v in seen if pick(v)}
                    if len(vals) == 1 and len({v[0] for v in seen if v[0]}) <= 1:
                        t, isin, cusip = sorted(seen)[0]
                        it["ticker"], it["isin"], it["cusip"] = t, isin, cusip
                        break
        sid = sec_id(it["isin"], it["cusip"])
        if not it["sector"] and it["kind"] == "security":
            it["sector"] = idx.sector_by_key.get(sid, "") if sid else ""
            for nk in ([] if it["sector"] else name_keys(it["name"])):
                it["sector"] = idx.sector_by_name.get(nk, "")
                if it["sector"]:
                    break
        items.append(it)
    tot = sum(it["w"] for it in items)
    scale = 1.0
    info = idx.funds.get(key) or {}
    proxy = bool(entry.get("proxy"))
    lev, basis = leverage_of([(it["w"], True) for it in items], entry.get("name") or info.get("name") or "", entry.get("issuer") or info.get("issuer") or "", False)
    src = entry["source"]
    if proxy and not src.upper().startswith("PROXY"):
        src = "PROXY: " + src
    return {"key": key, "market": m, "ticker": key.split(":", 1)[1], "issuer": entry.get("issuer") or info.get("issuer") or "", "source": src, "url": entry.get("url") or "",
            "as_of": entry.get("as_of") or "", "n_rows": len(items), "raw_sum": round(tot, 4), "coverage": entry.get("coverage") or "full schedule of investments",
            "partial": False, "scale": scale, "disclosed_pct": round(tot, 4), "leverage": lev, "leverage_basis": basis,
            "note": entry.get("note") or None, "proxy": proxy, "replaces": (info.get("source") or "") if info else "", "holdings": items}


def build_bundle(xray_dir: Path, tickers_yaml: Path, log=print, supplements_dir: Optional[Path] = None) -> dict:
    import yaml
    idx = XrayIndex(xray_dir)
    if supplements_dir is None:
        supplements_dir = Path(tickers_yaml).parent / "holdings_supplements"
    supp = load_supplements(supplements_dir)
    tails = {k: e for k, e in supp.items() if e.get("mode") == "tail_proxy"}
    if tails:
        need = set(tails) | {e["proxy_of"] for e in tails.values()}
        trows = idx.rows_for(need)
        for k, e in tails.items():
            supp[k] = make_tail_proxy(idx, k, e, trows)
            log(f"tail proxy {k}: {supp[k]['note']}")
    log(f"supplements: {len(supp)} funds from {supplements_dir}")
    uni = yaml.safe_load(Path(tickers_yaml).read_text(encoding="utf-8"))
    syms = [f["ticker"] for f in uni["funds"]]
    aliases = dashboard_aliases(syms, idx, set(supp))
    wanted = set(aliases.values())
    have: dict[str, list] = {}
    for it in range(MAX_DEPTH + 1):
        rows = idx.rows_for((wanted - set(have)) - set(supp))
        have.update(rows)
        for k in wanted & set(supp):
            have.setdefault(k, [])
        new = set()
        for k, rs in rows.items():
            m = k.split(":")[0]
            for r in rs:
                sub = idx.resolve_fund(r, m, k)
                if sub and sub in idx.funds and sub not in have and sub not in wanted:
                    new.add(sub)
        for k in wanted & set(supp):
            for sub in supp_subs(idx, k, supp[k]).values():
                if (sub in idx.funds or sub in supp) and sub not in have and sub not in wanted:
                    new.add(sub)
        log(f"pass {it}: {len(rows)} funds read, {len(new)} new sub-funds")
        if not new:
            break
        wanted |= new
    funds = {}
    for k, rs in have.items():
        f = build_supp_fund(idx, k, supp[k]) if k in supp else build_fund(idx, k, rs)
        f["name"] = (idx.univ.get((k.split(":")[0], k.split(":", 1)[1].upper())) or {}).get("name") or (supp.get(k) or {}).get("name") or ""
        funds[k] = f
    for f in funds.values():  # sub-funds we hold no rows for: physical bullion/crypto trusts are terminal, anything else stays opaque
        for it in f["holdings"]:
            if it["kind"] == "fund" and not it["sub"] and BULLION_RX.search(it["name"]):
                it["kind"], it["asset_class"] = "security", it["asset_class"] or "Commodity"
            if it["sub"] and it["sub"] not in funds:
                if BULLION_RX.search(it["name"]):
                    it["kind"], it["asset_class"] = "security", it["asset_class"] or "Commodity"
                it["unlisted_fund"] = it["sub"]
                it["sub"] = None
    return {"format": FORMAT, "generated": datetime.now().isoformat(timespec="seconds"), "xray_built": idx.meta.get("built"),
            "source_note": "portfolio-xray index (issuer sites, SEC EDGAR N-PORT, Cboe Canada); see per-fund source and as_of",
            "funds": funds, "aliases": aliases}


def write_bundle(bundle: dict, path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(bundle, fh, separators=(",", ":"))
