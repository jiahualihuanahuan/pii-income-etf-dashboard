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
                        sec = norm_sector(r[8])
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


def dashboard_aliases(symbols: Iterable[str], idx: XrayIndex) -> dict[str, str]:
    """dashboard symbol -> xray fund key. US listings match US only; X.TO -> CA:X; X-U.TO -> its CA share-class twin."""
    out: dict[str, str] = {}
    for s in symbols:
        if s.endswith(".TO"):
            b = s[:-3]
            for c in ([b.replace("-U", ".U"), b[:-2]] if b.endswith("-U") else []) + [b]:
                k = idx.key_for("CA", c)
                if k:
                    out[s] = k
                    break
        else:
            k = idx.key_for("US", s)
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


def build_bundle(xray_dir: Path, tickers_yaml: Path, log=print) -> dict:
    import yaml
    idx = XrayIndex(xray_dir)
    uni = yaml.safe_load(Path(tickers_yaml).read_text(encoding="utf-8"))
    syms = [f["ticker"] for f in uni["funds"]]
    aliases = dashboard_aliases(syms, idx)
    wanted = set(aliases.values())
    have: dict[str, list] = {}
    for it in range(MAX_DEPTH + 1):
        rows = idx.rows_for(wanted - set(have))
        have.update(rows)
        new = set()
        for k, rs in rows.items():
            m = k.split(":")[0]
            for r in rs:
                sub = idx.resolve_fund(r, m, k)
                if sub and sub in idx.funds and sub not in have and sub not in wanted:
                    new.add(sub)
        log(f"pass {it}: {len(rows)} funds read, {len(new)} new sub-funds")
        if not new:
            break
        wanted |= new
    funds = {}
    for k, rs in have.items():
        f = build_fund(idx, k, rs)
        f["name"] = (idx.univ.get((k.split(":")[0], k.split(":", 1)[1].upper())) or {}).get("name") or ""
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
