"""Parse the 'Schedule of Investments' of Canadian fund financial statements (text from ``pdftotext -layout``).

Handles the layout used by Global X Canada (security, shares/contracts, average cost, fair value) with country / sector sub-headings, a
DERIVATIVES section (written options, negative fair value) and the closing lines (Cash, Other assets less liabilities, NET ASSETS). Weights are
fair value / net assets in percent, so everything adds to 100 and negative 'other assets less liabilities' shows borrowing/leverage.
Pure functions, no I/O.
"""
from __future__ import annotations

import re
from typing import Optional

NUM = r"\(?-?[\d,]+(?:\.\d+)?\)?"
ROW = re.compile(rf"^\s{{2,}}(?P<name>\S.*?)\s+(?P<shares>{NUM})\s+\$?\s*(?P<cost>{NUM})\s+\$?\s*(?P<fv>{NUM})\s*$")
ROW2 = re.compile(rf"^\s{{2,}}(?P<name>\S.*?)\s{{2,}}(?P<cost>{NUM})\s+\$?\s*(?P<fv>{NUM})\s*$")  # no share count column
HEAD = re.compile(r"^\s*(?P<label>[A-Za-z][^()$]*?)\s*\((?P<pct>-?[\d.]+)%\)\s*$")
CLOSE = re.compile(r"^\s*(?P<label>Cash and cash equivalents|Margin deposits|Other assets less liabilities|NET ASSETS|TOTAL INVESTMENT PORTFOLIO)\s*\((?P<pct>-?[\d.]+)%\)\s*\$?\s*(?P<fv>" + NUM + r")?\s*(?:\$?\s*(?P<fv2>" + NUM + r"))?\s*$", re.I)
COUNTRIES = {"canada", "united states", "u.s.", "united kingdom", "south africa", "ireland", "switzerland", "netherlands", "bermuda", "jersey", "australia", "japan", "china", "peru",
             "mexico", "brazil", "france", "germany", "italy", "spain", "sweden", "hong kong", "taiwan", "korea", "india", "israel", "singapore", "luxembourg", "cayman islands", "panama",
             "guernsey", "norway", "denmark", "finland", "belgium", "austria", "portugal", "new zealand", "greece", "chile", "colombia", "argentina", "turkey", "poland", "united arab emirates"}


def num(s: str) -> float:
    s = s.strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    v = float(re.sub(r"[(),$\-\s]", "", s) or 0)
    return -v if neg else v


def _blocks(text: str) -> list[list[str]]:
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(r"^\s*Schedule of Investments", l)]
    out = []
    for s in starts:
        blk = []
        for l in lines[s + 1:]:
            if re.match(r"^\s*Notes to Financial Statements", l):
                break
            blk.append(l)
            if re.match(r"^\s*NET ASSETS\b", l):
                break
        out.append(blk)
    return out


def parse_schedule(text: str) -> dict:
    """-> {'as_of': 'YYYY-MM-DD'|None, 'net_assets': float, 'rows': [...], 'check': {...}}. First fund in the text."""
    as_of = None
    m = re.search(r"As at ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if m:
        from datetime import datetime
        as_of = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    rows, closing = [], {}
    section = sub = None
    country = sector = None
    deriv = False
    seen_start = False
    blocks = _blocks(text)
    # a fund's schedule can span a page break: concatenate blocks until the first NET ASSETS line
    lines = []
    for b in blocks:
        lines += b
        if any(re.match(r"^\s*NET ASSETS\b", l) for l in b):
            break
    for l in lines:
        if not l.strip() or "Security" in l and "Fair" not in l and "Shares" in l:
            continue
        c = CLOSE.match(l)
        if c:
            lab = c.group("label").upper()
            fv = c.group("fv2") or c.group("fv")
            closing[lab] = (float(c.group("pct")), num(fv) if fv else None)
            continue
        if re.search(r"^\s*TOTAL\b|Transaction Costs|^\s*(Average|Fair|Value|Shares|Cost|Contracts)\b|Average\s+Fair|^\s*Security\s", l, re.I) and not ROW.match(l):
            continue
        r = ROW.match(l)
        if r:
            name = r.group("name").strip()
            fv = num(r.group("fv"))
            rows.append({"name": name, "shares": num(r.group("shares")), "fv": fv, "section": section, "sub": sub, "country": country, "sector": sector, "deriv": deriv})
            continue
        h = HEAD.match(l)
        if h:
            label, pct = h.group("label").strip(), float(h.group("pct"))
            ind = len(l) - len(l.lstrip())
            up = label.upper()
            if up.startswith("DERIVATIVES") or up in ("SHORT POSITIONS", "LONG POSITIONS") or "OPTIONS" in up or "FUTURES" in up or "FORWARD" in up:
                deriv = True
                continue
            if label == label.upper() and ("EQUITIES" in up or "FUNDS" in up or "ETF" in up or "BONDS" in up or "FIXED" in up or "COMMODIT" in up or "CASH" in up or "SHORT-TERM" in up or "TREASURY" in up):
                section, country, sector, deriv = label, None, None, False
                if up.startswith("CANADIAN"):
                    country = "Canada"
                elif up.startswith("U.S."):
                    country = "United States"
                continue
            if label.lower() in COUNTRIES:
                country = "United States" if label.lower() == "u.s." else label
            else:
                sector = label
            continue
    na = closing.get("NET ASSETS", (100.0, None))[1]
    if not na:
        raise ValueError("NET ASSETS not found")
    for r in rows:
        r["w"] = r["fv"] / na * 100.0
    other = [(k, v) for k, v in closing.items() if k in ("CASH AND CASH EQUIVALENTS", "MARGIN DEPOSITS", "OTHER ASSETS LESS LIABILITIES") and v[1] is not None]
    total_w = sum(r["w"] for r in rows) + sum(v[1] / na * 100 for _, v in other)
    stated = closing.get("TOTAL INVESTMENT PORTFOLIO", (None, None))[0]
    inv_w = sum(r["w"] for r in rows)
    return {"as_of": as_of, "net_assets": na, "rows": rows, "cash": {k: v[1] / na * 100 for k, v in other},
            "check": {"rows_pct": round(inv_w, 3), "stated_portfolio_pct": stated, "total_pct": round(total_w, 3), "n": len(rows)}}


FUND_RX = re.compile(r"\b(ETF|FUND|TRUST|INDEX ETF|UNITS?)\b", re.I)


def to_holdings(parsed: dict) -> list[dict]:
    """Bundle-style holdings (weights in % of NAV). kind: security | fund | derivative | cash."""
    out = []
    for r in parsed["rows"]:
        name = re.sub(r"\s+", " ", r["name"]).strip()
        is_fund = bool(re.search(r"\b(ETF|ETFs)\b", name))
        kind = "derivative" if r["deriv"] else "fund" if is_fund else "security"
        out.append({"name": name, "ticker": "", "isin": "", "cusip": "", "w": round(r["w"], 6), "sector": "" if kind != "security" else (r["sector"] or ""),
                    "country": r["country"] or "", "asset_class": "Derivative" if kind == "derivative" else "", "currency": "", "kind": kind, "sub": None})
    resid = 100.0 - sum(o["w"] for o in out) - sum(parsed["cash"].values())
    if 0.005 < abs(resid) < 0.5:  # statements round; keep the total at 100% of NAV
        parsed["cash"]["OTHER ASSETS LESS LIABILITIES"] = parsed["cash"].get("OTHER ASSETS LESS LIABILITIES", 0.0) + resid
    for k, w in parsed["cash"].items():
        nm = ("Cash and cash equivalents" if k.startswith("CASH") else "Margin deposits" if k.startswith("MARGIN") else "Other assets less liabilities"
              if k.startswith("OTHER ASSETS") else k)
        out.append({"name": nm, "ticker": "", "isin": "", "cusip": "", "w": round(w, 6), "sector": "", "country": "", "asset_class": "Cash", "currency": "", "kind": "cash", "sub": None})
    return out


# ---- Brompton-style statements: "Number of Shares | Cost | Carrying Value | % of Net Assets" (no % on the rows) ----
BROW = re.compile(rf"^\s{{2,}}(?P<name>\S.*?)(?:\s{{2,}}|\s(?=\$))(?:\$\s*)?(?P<shares>{NUM})\s+(?P<cost>{NUM})\s+(?P<cv>{NUM})\s*(?P<pct>\(?-?\d+\.\d\)?)?\s*$")
BROW2 = re.compile(rf"^\s{{2,}}(?P<name>[A-Za-z].*?)\s{{2,}}(?P<cost>{NUM})\s+(?P<cv>{NUM})\s*$")  # no share/par column
CLOSE_LABEL = re.compile(rf"^\s*(?P<label>[A-Za-z][A-Za-z .,&()\d\-]*?)\s{{2,}}(?P<rest>(?:{NUM}\s*)+)$")
REGIONS = {"asia", "europe", "north america", "oceania", "latin america", "africa", "middle east", "south america", "central america", "caribbean"}
MONTH_END = re.compile(r"^((January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}|due\b.*|\d{4})$")


def _bval(line: str) -> float:
    """Dollar value of a closing line: drop the label and the trailing '% of net assets' token, take the last number."""
    toks = re.findall(r"\(?-?[\d,]+(?:\.\d+)?\)?", re.sub(r"\((note|schedule) \d+\)", "", line))
    toks = [t for t in toks if re.search(r"\d", t)]
    if len(toks) > 1 and re.fullmatch(r"\(?-?\d+\.\d\)?", toks[-1]):
        toks = toks[:-1]
    return num(toks[-1])


def _strip_fn(name: str) -> str:
    return re.sub(r"(?<=[A-Za-z\)])[1-9]$", "", name.strip())  # footnote marker glued to the name ("ETF2")


def _heading(t: str) -> str:
    t = re.sub(r"\s*\(cont.d\)", "", t)
    return re.sub(r"\s{2,}Par .*$", "", t).strip()


def _walk_rows(seg: list[str], na: float = 0.0, with_closing: bool = False):
    rows, cash, closing_deriv = [], {}, []
    pend = None
    country = sector = None
    after_total = False
    for l in seg:
        t = l.strip()
        if not t or re.fullmatch(r"(?:Number of|Shares/?|Units|Shares/Units|Par Value|Par|Cost\d?|Value|Carrying|CAD\$|US\$|\$|Assets|Portfolio|%\s*of(?:\s*Net)?|\s)+", t) or re.match(r"^(As at|SCHEDULE|Brompton .* - Interim)", t) or re.fullmatch(r"\d+", t):
            continue
        if re.match(r"^Total Investments", t):
            after_total = True
            continue
        if after_total:
            c = CLOSE_LABEL.match(l)
            if c and not t.startswith("Net Assets"):
                lab = re.sub(r"\s*\((note|schedule) \d+\)", "", c.group("label")).strip()
                w = _bval(l) / na * 100
                if re.search(r"option|forward|future|swap", lab, re.I):
                    closing_deriv.append({"name": lab, "shares": 0.0, "fv": _bval(l), "section": None, "sub": None, "country": None, "sector": None, "deriv": True, "w": w})
                else:
                    cash[lab] = w
            continue
        if re.match(r"^(Total|Embedded)", t):
            continue
        r = BROW.match(l) or BROW2.match(l)
        if r and not re.fullmatch(r"[\d,().\s-]+", r.group("name").strip()):
            name = _strip_fn(r.group("name"))
            if pend:
                name = pend + " " + name
                pend = None
            rows.append({"name": name, "shares": num(r.groupdict().get("shares") or "0"), "fv": num(r.group("cv")), "section": None, "sub": None, "country": country,
                         "sector": sector, "deriv": False, "_wrapped": False})
            continue
        if re.match(r"^\d", t) or re.search(r"\d{1,3}(,\d{3})+", t):
            continue  # subtotal line
        ind = len(l) - len(l.lstrip())
        h = _heading(t)
        if ind <= 4 and not (rows and ind > 0):
            hl = h.lower()
            if hl in REGIONS:
                pass
            elif hl in COUNTRIES or hl in ("great britain", "united kingdom"):
                country = "United Kingdom" if hl == "great britain" else "United States" if hl in ("u.s.", "united states") else h
            else:
                sector = h
        elif rows and not rows[-1]["_wrapped"] and (MONTH_END.match(t) or (len(t) <= 30 and re.search(r"(ETF|Fund|Trust)\d?$", t) and not re.search(r"(ETF|Fund|Trust)$", rows[-1]["name"]))):
            rows[-1]["name"] = _strip_fn(rows[-1]["name"] + " " + t)
        else:
            pend = t
    return (rows, cash, closing_deriv) if with_closing else rows


def parse_brompton_schedule(text: str, title: str) -> dict:
    """Schedule of investment portfolio of one fund inside a (combined) Brompton financial-statements text.

    Row weight = carrying value / net assets. Closing lines (options, forwards, cash, loans, other net assets) are kept so the total is 100% of NAV.
    """
    lines = text.splitlines()
    start = None
    for i, l in enumerate(lines):
        if l.strip().startswith(title) and "Interim Financial Statements" in l and i + 1 < len(lines) and lines[i + 1].strip().startswith("SCHEDULE OF INVESTMENT PORTFOLIO"):
            start = i
            break
    if start is None:
        raise ValueError(f"schedule for {title!r} not found")
    as_of = None
    m = re.search(r"As at ([A-Z][a-z]+ \d{1,2}, \d{4})", "\n".join(lines[start:start + 6]))
    if m:
        from datetime import datetime
        as_of = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    seg, na = [], None
    for l in lines[start + 2:]:
        seg.append(l)
        if re.match(r"^\s*Net Assets attributable to holders", l) and re.search(r"\d", l):
            na = _bval(l)
            break
    if not na:
        raise ValueError("net assets not found")
    rows, cash, closing_deriv = _walk_rows(seg, na, with_closing=True)
    for r in rows:
        r["w"] = r["fv"] / na * 100.0
    rows += closing_deriv
    inv = sum(r["w"] for r in rows)
    return {"as_of": as_of, "net_assets": na, "rows": rows, "cash": cash,
            "check": {"rows_pct": round(inv, 3), "stated_portfolio_pct": None, "total_pct": round(inv + sum(cash.values()), 3), "n": len(rows)}}


# ---- split-share corporations (Class A shares sit behind Preferred shares) ----
def _bs_first(text: str, label_rx: str, after: int = 0) -> Optional[float]:
    """First number (current-period column) on the first line after ``after`` that starts with the label."""
    for l in text[after:].splitlines():
        if re.match(rf"^\s*(?:{label_rx})(?=[\s(,$])", l):
            rest = re.sub(r"\((?:schedule|note)\s*\d+\)", "", re.sub(rf"^\s*(?:{label_rx})", "", l))
            m = re.search(r"\(?-?[\d,]{2,}(?:\.\d+)?\)?", rest)
            if m:
                return num(m.group(0))
    return None


SECTOR_HEADS = {"Consumer Discretionary", "Consumer Staples", "Communication Services", "Energy", "Financials", "Healthcare", "Health Care", "Industrials",
                "Information Technology", "Investment Funds", "Materials", "Real Estate", "Utilities", "Technology", "Telecommunication Services", "Other"}


def parse_split_corp_brompton(text: str, title: str) -> dict:
    """Brompton split corporation interim report: investments listed as '% of Portfolio'; re-based to Class A net assets.

    Weights are % of the *Class A* net assets (the instrument the dashboard tracks): the Preferred shares ranking ahead of Class A show up as a
    negative row ("Preferred shares and other net liabilities"), so the net is 100% and leverage = gross / net, like any levered ETF.
    """
    lines = text.splitlines()
    start = None
    for i, l in enumerate(lines):
        if l.strip().startswith(title) and re.search(r"- (Interim Report|Interim Financial Statements|Annual Report) \d{4}", l) and i + 1 < len(lines) and lines[i + 1].strip().startswith("SCHEDULE OF INVESTMENT PORTFOLIO"):
            start = i
            break
    if start is None:
        raise ValueError(f"schedule for {title!r} not found")
    as_of = None
    m = re.search(r"As at ([A-Z][a-z]+ \d{1,2}, \d{4})", "\n".join(lines[start:start + 6]))
    if m:
        from datetime import datetime
        as_of = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
    head = "\n".join(lines[:start])
    pos = head.find("STATEMENTS OF FINANCIAL POSITION")
    if pos < 0:
        raise ValueError("statement of financial position not found")
    na = _bs_first(head, r"Net Assets attributable to holders of redeemable Class A shares", pos)
    cash = _bs_first(head, r"Cash", pos) or 0.0
    opts = _bs_first(head, r"Option contracts written, at fair value[^$\d]*", pos) or 0.0
    fwd_gain = _bs_first(head, r"Unrealized gain on foreign currency forward contracts[^$\d]*", pos) or 0.0
    fwd_loss = _bs_first(head, r"Unrealized loss on foreign currency forward contracts[^$\d]*", pos) or 0.0
    if not na:
        raise ValueError("Class A net assets not found")
    seg = []
    for l in lines[start + 2:]:
        seg.append(l)
        if re.match(r"^\s*Total Investments", l):
            break
    rows = _walk_rows(seg)
    heads = {l.strip() for l in seg if l.strip() in SECTOR_HEADS}
    for r in rows:  # a section heading line directly above its first row is glued onto the name by the line walker
        for h in sorted(heads, key=len, reverse=True):
            if r["name"].startswith(h + " ") and r["name"][len(h) + 1:][:1].isupper():
                r["name"] = r["name"][len(h) + 1:]
                r["sector"] = r["sector"] or h
                break
        r["w"] = r["fv"] / na * 100.0
    derivs = []
    if opts:
        derivs.append({"name": "Call options written", "shares": 0.0, "fv": -opts, "section": None, "sub": None, "country": None, "sector": None, "deriv": True, "w": -opts / na * 100})
    if fwd_gain or fwd_loss:
        derivs.append({"name": "Net foreign currency forward contracts", "shares": 0.0, "fv": fwd_gain - fwd_loss, "section": None, "sub": None, "country": None, "sector": None,
                       "deriv": True, "w": (fwd_gain - fwd_loss) / na * 100})
    inv = sum(r["w"] for r in rows)
    cash_w = cash / na * 100
    resid = 100.0 - inv - sum(d["w"] for d in derivs) - cash_w
    rows += derivs
    return {"as_of": as_of, "net_assets": na, "rows": rows,
            "cash": {"Cash": cash_w, "Preferred shares and other net liabilities (split-share leverage)": resid},
            "check": {"rows_pct": round(inv, 3), "stated_portfolio_pct": None, "total_pct": 100.0, "n": len(rows) - len(derivs)}}


def _q_first(text: str, label_rx: str, after: int = 0) -> Optional[float]:
    for l in text[after:].replace("\t", " ").splitlines():
        m = re.match(rf"^\s*(?:{label_rx})\b", l, re.I)
        if m:
            rest = re.sub(r"\((?:notes?|schedule)\s*[\d, and]+\)|\(\d\)", "", l[m.end():], flags=re.I)
            n = re.search(r"\(?-?[\d,]{3,}(?:\.\d+)?\)?", rest)
            if n:
                return num(n.group(0))
    return None


QROW = re.compile(r"^\s*(?P<shares>[\d,]+)\s+(?P<name>[A-Za-z].*?)\s{2,}(?P<cost>[\d,]+)\s+(?P<fv>[\d,]+)\s*$")


def parse_split_corp_quadravest(text: str) -> dict:
    """Quadravest split corporation semi-annual/annual report (Financial 15, Dividend 15, North American Financial 15...).

    Same re-basing as parse_split_corp_brompton: weights are % of Class A net assets; Preferred shares appear as the negative remainder.
    """
    t = text.replace("\t", " ")
    lines = t.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().startswith("Schedule of Portfolio Investments") and "continued" not in l), None)
    if start is None:
        raise ValueError("schedule not found")
    m = re.search(r"As at ([A-Z][a-z]+ \d{1,2}, \d{4})", "\n".join(lines[start:start + 4]))
    from datetime import datetime
    as_of = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat() if m else None
    head = "\n".join(lines[:start])
    pos = head.find("Statements of Financial Position")
    na = _q_first(head, r"OF REDEEMABLE CLASS A SHARES", pos)
    cash = _q_first(head, r"Cash", pos) or 0.0
    opts = _q_first(head, r"Written options", pos) or 0.0
    if not na:
        raise ValueError("Class A net assets not found")
    rows, country = [], None
    for l in lines[start + 1:]:
        s = l.strip()
        if re.match(r"^Total Common Equities", s) or s.startswith("Call Options written"):
            break
        if re.search(r"Canadian .*Common Equities$", s) and not s.startswith("Total"):
            country = "Canada"
        elif re.search(r"U\.S\. .*Common Equities$", s) and not s.startswith("Total"):
            country = "United States"
        r = QROW.match(l)
        if r and not s.startswith("Total"):
            rows.append({"name": r.group("name").strip(), "shares": num(r.group("shares")), "fv": num(r.group("fv")), "section": None, "sub": None, "country": country,
                         "sector": None, "deriv": False})
    for r in rows:
        r["w"] = r["fv"] / na * 100.0
    derivs = [{"name": "Call options written", "shares": 0.0, "fv": -opts, "section": None, "sub": None, "country": None, "sector": None, "deriv": True, "w": -opts / na * 100}] if opts else []
    inv = sum(r["w"] for r in rows)
    cash_w = cash / na * 100
    resid = 100.0 - inv - sum(d["w"] for d in derivs) - cash_w
    return {"as_of": as_of, "net_assets": na, "rows": rows + derivs,
            "cash": {"Cash": cash_w, "Preferred shares and other net liabilities (split-share leverage)": resid},
            "check": {"rows_pct": round(inv, 3), "stated_portfolio_pct": None, "total_pct": 100.0, "n": len(rows)}}


def _split_rebase(as_of, na, rows, cash, extra_derivs=None):
    for r in rows:
        r["w"] = r["fv"] / na * 100.0
    derivs = extra_derivs or []
    inv = sum(r["w"] for r in rows)
    cash_w = cash / na * 100
    resid = 100.0 - inv - sum(d["w"] for d in derivs) - cash_w
    return {"as_of": as_of, "net_assets": na, "rows": rows + derivs,
            "cash": {"Cash": cash_w, "Preferred shares and other net liabilities (split-share leverage)": resid},
            "check": {"rows_pct": round(inv, 3), "stated_portfolio_pct": None, "total_pct": 100.0, "n": len(rows)}}


def _iso(s: str) -> Optional[str]:
    from datetime import datetime
    m = re.search(r"([A-Za-z]+ \d{1,2}, \d{4})", s or "")
    return datetime.strptime(m.group(1).title(), "%B %d, %Y").date().isoformat() if m else None


def parse_split_corp_middlefield(text: str) -> dict:
    """Middlefield split corporation interim/annual report (Real Estate Split Corp. ...). Weights are % of Class A net assets."""
    lines = text.replace("\t", " ").splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "Schedule of Investment Portfolio")
    as_of = _iso(lines[start + 1])
    head = "\n".join(lines[:start])
    pos = head.find("Statements of Financial Position")
    na = _q_first(head, r"Net Assets Attributable to Holders of Redeemable\s+Class A Shares", pos) or _q_first(head, r"Net Assets Attributable to Holders of Redeemable Class A Shares", pos)
    cash = _q_first(head, r"Cash", pos) or 0.0
    if not na:
        raise ValueError("Class A net assets not found")
    rx = re.compile(r"^\s*(?P<name>[A-Za-z][^$]*?)\s{2,}(?P<shares>[\d,]+)\s+\$?\s*(?P<cost>[\d,]+)\s+\$?\s*(?P<fv>[\d,]+)\s*$")
    rows = []
    for l in lines[start + 1:]:
        if l.strip().upper().startswith("TOTAL INVESTMENTS"):
            break
        r = rx.match(l)
        if r:
            rows.append({"name": r.group("name").strip(), "shares": num(r.group("shares")), "fv": num(r.group("fv")), "section": None, "sub": None,
                         "country": None, "sector": None, "deriv": False})
    return _split_rebase(as_of, na, rows, cash)


def parse_split_corp_ninepoint(text: str) -> dict:
    """Ninepoint split corporation interim report (Canadian Large Cap Leaders Split Corp.)."""
    lines = text.replace("\t", " ").splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "Schedule of Investment Portfolio")
    as_of = _iso(lines[start + 1])
    pos = next(i for i, l in enumerate(lines) if l.strip() == "Statements of Financial Position")
    head = "\n".join(lines)
    off = len("\n".join(lines[:pos]))
    na = _q_first(head, r"Net Assets attributable to holders of redeemable Class A shares", off)
    cash = _q_first(head, r"Cash", off) or 0.0
    if not na:
        raise ValueError("Class A net assets not found")
    rx = re.compile(r"^\s*(?P<shares>[\d,]+)\s+(?P<name>[A-Za-z].*?)\s{2,}(?P<cost>[\d,]+)\s+(?P<fv>[\d,]+)\s*$")
    rows, sector = [], None
    for l in lines[start + 1:]:
        s = l.strip()
        if s.startswith("Total equities") or s.startswith("Total investments"):
            break
        m = re.match(r"^([A-Z][A-Z &]+) \[[\d.]+%\]$", s)
        if m and m.group(1) != "EQUITIES":
            sector = m.group(1).title()
        r = rx.match(l)
        if r:
            rows.append({"name": r.group("name").strip(), "shares": num(r.group("shares")), "fv": num(r.group("fv")), "section": None, "sub": None,
                         "country": "Canada", "sector": sector, "deriv": False})
    return _split_rebase(as_of, na, rows, cash)
