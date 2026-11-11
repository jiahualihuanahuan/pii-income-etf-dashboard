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
    rows, cash, closing_deriv = [], {}, []
    pend = None
    country = sector = None
    after_total = False
    for l in seg:
        t = l.strip()
        if not t or re.fullmatch(r"(?:Number of|Shares/?|Units|Par Value|Par|Cost\d?|Value|Carrying|CAD\$|US\$|\$|Assets|%\s*of\s*Net|\s)+", t) or re.match(r"^(As at|SCHEDULE|Brompton .* - Interim)", t) or re.fullmatch(r"\d+", t):
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
    for r in rows:
        r["w"] = r["fv"] / na * 100.0
    rows += closing_deriv
    inv = sum(r["w"] for r in rows)
    return {"as_of": as_of, "net_assets": na, "rows": rows, "cash": cash,
            "check": {"rows_pct": round(inv, 3), "stated_portfolio_pct": None, "total_pct": round(inv + sum(cash.values()), 3), "n": len(rows)}}
