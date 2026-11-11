"""SEC N-PORT (primary_doc.xml) -> supplement entry for funds the xray index lacks (US option-income ETFs).

Weights are N-PORT ``pctVal`` (% of net assets, as filed: options and written options carry their own sign).
"""
from __future__ import annotations

import re
from typing import Optional
from xml.etree import ElementTree as ET

NS = "{http://www.sec.gov/edgar/nport}"
DERIV_CATS = {"DE", "DIR", "DFE", "DCR", "DCO", "DO", "DC", "DIR "}
_DERIV_BLOCKS = ("optionSwaptionWarrantDeriv", "futureDeriv", "swapDeriv", "fwdDeriv", "forwardDeriv", "otherDeriv", "swaptionDeriv")


def _t(e, tag: str) -> str:
    x = e.find(NS + tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def _fl(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_nport(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    gi = root.find(f".//{NS}genInfo")
    fi = root.find(f".//{NS}fundInfo")
    as_of = _t(gi, "repPdDate")
    out = {"series": _t(gi, "seriesName"), "as_of": as_of, "net_assets": _fl(_t(fi, "netAssets")), "rows": []}
    for s in root.iter(NS + "invstOrSec"):
        w = _fl(_t(s, "pctVal"))
        if w is None:
            continue
        ids = s.find(NS + "identifiers")
        isin = tk = ""
        if ids is not None:
            x = ids.find(NS + "isin")
            isin = x.get("value", "") if x is not None else ""
            x = ids.find(NS + "ticker")
            tk = x.get("value", "") if x is not None else ""
        ac = _t(s, "assetCat") or ("OTHER" if s.find(NS + "assetConditional") is not None else "")
        ic = _t(s, "issuerCat") or ("OTHER" if s.find(NS + "issuerConditional") is not None else "")
        has_deriv = any(s.find(NS + b) is not None for b in _DERIV_BLOCKS)
        name = _t(s, "name") or _t(s, "title")
        title = _t(s, "title")
        out["rows"].append({"name": name, "title": title, "cusip": _t(s, "cusip") if _t(s, "cusip") != "000000000" else "", "isin": isin, "ticker": tk, "w": w,
                            "asset_cat": ac, "issuer_cat": ic, "country": _t(s, "invCountry"), "payoff": _t(s, "payoffProfile"), "deriv": has_deriv or ac in DERIV_CATS})
    return out


FUND_RX = re.compile(r"\b(ETF|ETFS|TRUST|FUND)\b", re.I)


def to_holdings(p: dict) -> list[dict]:
    out = []
    for r in p["rows"]:
        ac, ic = r["asset_cat"], r["issuer_cat"]
        raw = r["name"] if r["name"].upper() not in ("N/A", "NA", "") else r["title"]
        name = re.sub(r"\s+", " ", raw).strip()
        name = name.title() if name.isupper() and len(name) > 4 and not r["deriv"] else name
        if r["deriv"]:
            kind, aclass = "derivative", "Derivative"
            desc = r["title"] if r["title"] and r["title"].upper() != raw.upper() else ""
            if desc:
                name = f"{name} - {desc}" if desc.lower() not in name.lower() else name
        elif ac in ("STIV", "RA"):
            kind, aclass = "cash", "Cash"
        elif ac == "DBT" and ic in ("UST", "USGA"):
            kind, aclass = "cash", "Cash"  # T-bills / government paper: shown with cash, not as stocks
            name = f"{name} ({r['title']})" if r["title"] and r["title"] != r["name"] else name
        elif ac in ("EC", "EP") and ic == "RF" and re.search(r"T-?BILL|TREASURY|MONEY MARKET|GOVERNMENT OBLIG", name, re.I):
            kind, aclass = "cash", "Cash"  # T-bill / money-market ETFs are cash management, not stock exposure
        elif ac in ("EC", "EP"):
            kind, aclass = ("fund" if ic == "RF" else "security"), "Equity"
        elif ac == "DBT":
            kind, aclass = "security", "Fixed income"
        elif ac in ("COMM",) or "BITCOIN" in r["name"].upper() or "GOLD" in r["name"].upper():
            kind, aclass = "security", "Commodity"
        else:
            kind, aclass = "security", ac or ""
        if kind == "fund" and not FUND_RX.search(name):
            kind = "security"
        if kind == "cash" and ac == "STIV" and r["title"] and r["title"].upper() != raw.upper():
            name = f"{name} ({r['title']})"
        out.append({"name": name, "ticker": "" if r["ticker"] in ("N/A", "-") else r["ticker"], "isin": r["isin"], "cusip": r["cusip"], "w": round(r["w"], 6), "sector": "",
                    "country": "" if r["country"] in ("", "N/A") else r["country"], "asset_class": aclass, "currency": "", "kind": kind, "sub": None})
    return out


def with_residual(holdings: list[dict]) -> list[dict]:
    """N-PORT lists only itemised positions: cash at the bank and other net assets are the remainder to 100% of net assets (stated, not guessed)."""
    resid = 100.0 - sum(h["w"] for h in holdings)
    if abs(resid) >= 0.005:
        holdings = holdings + [{"name": "Cash and other net assets (not itemised in N-PORT)", "ticker": "", "isin": "", "cusip": "", "w": round(resid, 6), "sector": "", "country": "",
                                "asset_class": "Cash", "currency": "", "kind": "cash", "sub": None}]
    return holdings
