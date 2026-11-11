#!/usr/bin/env python3
"""Download fund financial-statement PDFs and turn their 'Schedule of Investments' into config/holdings_supplements/*.json.

Global X Canada publishes only the top-10 holdings on its site, but its interim / annual financial statements (public PDFs) contain the complete
schedule of investments (every security, written options, cash, other assets less liabilities) as at the report date.
Needs ``pdftotext`` (poppler-utils) and network access. The generated JSON is committed; the PDFs are not.

    python tools/build_supplements.py --cache /tmp/gx_pdfs
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import schedule_parser as sp  # noqa: E402

BASE = "https://www.globalx.ca/wp-content/uploads/2026/09/"
# fund_key (xray index key) -> (PDF file name, dashboard-facing fund name)
GLOBALX_IR2026 = {
    "CA:USSX": ("USSX-USSX.U-EN-IR2026.pdf", "Global X S&P 500 Index ETF"),
    "CA:QQQX": ("QQQX-QQQX.U-EN-IR2026.pdf", "Global X NASDAQ-100 Index ETF"),
    "CA:CNDX": ("CNDX-EN-IR2026.pdf", "Global X S&P/TSX 60 Index ETF"),
    "CA:EAFX": ("EAFX-EAFX.U-EN-IR2026.pdf", "Global X MSCI EAFE Index ETF"),
    "CA:EMMX": ("EMMX-EMMX.U-EN-IR2026.pdf", "Global X MSCI Emerging Markets Index ETF"),
    "CA:NRGY": ("NRGY-EN-IR2026.pdf", "Global X Equal Weight Canadian Oil & Gas Index ETF"),
    "CA:GLDX": ("GLDX-EN-IR2026.pdf", "Global X Gold Producers Index ETF"),
    "CA:GLCC": ("GLCC-EN-IR2026.pdf", "Global X Gold Producer Equity Covered Call ETF"),
    "CA:ENCC": ("ENCC-EN-IR2026.pdf", "Global X Canadian Oil and Gas Equity Covered Call ETF"),
    "CA:CPCC": ("CPCC-EN-IR2026.pdf", "Global X Copper Producer Equity Covered Call ETF"),
}


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        dest.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    return dest


def pdf_text(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


BROMPTON_FS = "https://www.bromptongroup.com/wp-content/uploads/2026/08/"
# Brompton ETFs: one combined PDF holds every ETF's interim statements; the title selects the fund.
BROMPTON_IR2026 = {
    "CA:BMAX": ("Brompton-ETFs-Common-Financial-Statements-English.pdf", "Brompton Enhanced Multi-Asset Income ETF"),
    "CA:BDIV": ("Brompton-ETFs-Common-Financial-Statements-English.pdf", "Brompton Global Dividend Growth ETF"),
    "CA:BPRF": ("Brompton-ETFs-Common-Financial-Statements-English.pdf", "Brompton Flaherty & Crumrine Investment Grade Preferred ETF"),
    "CA:EDGF": ("Brompton-ETFs-Common-Financial-Statements-English.pdf", "Brompton European Dividend Growth ETF"),
}


def build(cache: Path, funds: dict, base: str, label: str, issuer: str = "globalx", brand: str = "Global X Canada", brompton: bool = False) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, (fn, name) in funds.items():
        url = base + fn
        text = pdf_text(fetch(url, cache / fn))
        p = sp.parse_brompton_schedule(text, name) if brompton else sp.parse_schedule(text)
        chk = p["check"]
        holdings = sp.to_holdings(p)
        assert abs(sum(h["w"] for h in holdings) - 100) < 0.01, (key, chk)
        out[key] = {
            "name": name, "issuer": issuer, "as_of": p["as_of"], "url": url,
            "source": f"{brand} {label} (unaudited): complete schedule of investments as at {p['as_of']}",
            "coverage": "full schedule of investments (every position, written options, cash); weights = fair value / net assets",
            "proxy": False, "net_assets_cad": p["net_assets"], "holdings": holdings,
        }
        print(f"{key:10} {p['as_of']} rows={chk['n']:4} stated={chk['stated_portfolio_pct']}")
    return out


# ---- split corporations: weights are % of Class A net assets (Preferred shares appear as the negative remainder, so net = 100%) ----
SPLIT_NOTE = ("Split corporation: the weights are % of CLASS A net assets (what a Class A holder owns). Preferred shares are a liability, shown as a negative "
              "'Preferred shares and other net liabilities' row, so the portfolio is ~160-220% invested and leverage = gross / net.")
BROMPTON_IR = "https://www.bromptongroup.com/wp-content/uploads/2026/08/{t}-2026-Interim-Report-English.pdf"
SPLIT_BROMPTON = {  # key: (file token, name)
    "CA:GDV": ("GDV", "Global Dividend Growth Split Corp."), "CA:DGS": ("DGS", "Dividend Growth Split Corp."), "CA:LBS": ("LBS", "Life & Banc Split Corp."),
    "CA:SBC": ("SBC", "Brompton Split Banc Corp."), "CA:LCS": ("LCS", "Brompton Lifeco Split Corp."),
}
SPLIT_QUADRAVEST = {  # key: (url, name)  Quadravest semi-annual reports (as at 2026-05-31)
    "CA:FTN": ("https://www.quadravest.com/_files/ugd/78f11d_903a5b88bb244e8ca53377929aeb64b3.pdf", "Financial 15 Split Corp."),
    "CA:DFN": ("https://www.quadravest.com/_files/ugd/78f11d_44960ad20ef64471b0327baac9a09693.pdf", "Dividend 15 Split Corp."),
    "CA:FFN": ("https://www.quadravest.com/_files/ugd/78f11d_26aac82f4f9d48a3bfd528c164904d55.pdf", "North American Financial 15 Split Corp."),
}
SPLIT_OTHER = {
    "CA:RS": ("https://middlefield.com/wp-content/uploads/2026/08/rs-semi2026.pdf", "Real Estate Split Corp.", "middlefield", "Middlefield", sp.parse_split_corp_middlefield),
    "CA:NPS": ("https://www.ninepoint.com/media/egwo1qzh/ninepoint_canadian-large-cap-leaders-mrfp-fs-english.pdf", "Canadian Large Cap Leaders Split Corp.",
               "ninepoint", "Ninepoint Partners", sp.parse_split_corp_ninepoint),
}


def split_entry(key, name, url, issuer, brand, parser, cache):
    fn = re.sub(r"[^A-Za-z0-9]+", "_", url.rsplit("/", 1)[-1])
    text = pdf_text(fetch(url, cache / f"{key.split(':')[1]}_{fn}"))
    p = parser(text)
    holdings = sp.to_holdings(p)
    assert abs(sum(h["w"] for h in holdings) - 100) < 0.01, (key, p["check"])
    print(f"{key:10} {p['as_of']} rows={p['check']['n']:3} invested={p['check']['rows_pct']}% of Class A NAV")
    return {"name": name, "issuer": issuer, "as_of": p["as_of"], "url": url,
            "source": f"{brand} {p['as_of']} financial statements (unaudited): complete schedule of investments, re-based to Class A net assets",
            "coverage": "full schedule of investments (every position, written options, cash); weights = fair value / Class A net assets", "proxy": False,
            "note": SPLIT_NOTE, "net_assets_cad": p["net_assets"], "holdings": holdings}


def build_split(cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for k, (t, name) in SPLIT_BROMPTON.items():
        out[k] = split_entry(k, name, BROMPTON_IR.format(t=t), "brompton", "Brompton Funds", (lambda text, _n=name: sp.parse_split_corp_brompton(text, _n)), cache)
    for k, (url, name) in SPLIT_QUADRAVEST.items():
        out[k] = split_entry(k, name, url, "quadravest", "Quadravest", sp.parse_split_corp_quadravest, cache)
    for k, (url, name, issuer, brand, fn) in SPLIT_OTHER.items():
        out[k] = split_entry(k, name, url, issuer, brand, fn, cache)
    return out


# USHI (Ninepoint Enhanced U.S. Equity HighShares ETF, launched 2026-09-14): the issuer's website lists its complete look-through equity exposure
# (25 stocks, as at 2026-09-30) and the Options and Cash & Equivalents lines; the weights add up to 100.00% (copied from the product page).
USHI_URL = "https://www.ninepoint.com/funds/ninepoint-enhanced-us-equity-highshares-etf/"
USHI_ROWS = [("Alphabet Class A", 7.4, "Communication Services"), ("Tesla Inc", 6.3, "Consumer Discretionary"), ("Microsoft", 6.2, "Information Technology"),
             ("Amazon.com Inc", 6.1, "Consumer Discretionary"), ("Palantir Technologies Class A", 6.0, "Information Technology"),
             ("Advanced Micro Devices", 6.0, "Information Technology"), ("NVIDIA", 5.7, "Information Technology"),
             ("CrowdStrike Holdings Class A", 5.6, "Information Technology"), ("GE Aerospace", 5.5, "Industrials"), ("Johnson & Johnson", 5.3, "Health Care"),
             ("Eli Lilly", 5.0, "Health Care"), ("Intel Corporation", 5.0, "Information Technology"), ("Apple", 5.0, "Information Technology"),
             ("Goldman Sachs Group", 4.6, "Financials"), ("JPMorgan Chase", 4.5, "Financials"), ("Visa Class A", 4.5, "Financials"),
             ("Micron Technology", 4.4, "Information Technology"), ("Union Pacific", 4.4, "Industrials"), ("Costco Wholesale Corp", 3.5, "Consumer Staples"),
             ("Freeport-McMoRan Inc", 3.4, "Materials"), ("Vistra", 3.3, "Utilities"), ("ExxonMobil Holdings Corp", 3.3, "Energy"),
             ("Walmart", 3.2, "Consumer Staples"), ("Caterpillar Inc", 1.9, "Industrials"), ("GE Vernova", 1.6, "Industrials")]


def build_ushi() -> dict:
    h = [{"name": n, "ticker": "", "isin": "", "cusip": "", "w": w, "sector": sec, "country": "United States", "asset_class": "", "currency": "", "kind": "security", "sub": None}
         for n, w, sec in USHI_ROWS]
    h.append({"name": "Options (covered calls)", "ticker": "", "isin": "", "cusip": "", "w": -0.67, "sector": "", "country": "", "asset_class": "Derivative", "currency": "", "kind": "derivative", "sub": None})
    h.append({"name": "Cash and equivalents (net of borrowing; incl. rounding of published weights)", "ticker": "", "isin": "", "cusip": "", "w": -17.26, "sector": "", "country": "", "asset_class": "Cash", "currency": "", "kind": "cash", "sub": None})
    tot = sum(x["w"] for x in h)
    assert abs(tot - 100) < 0.5, tot
    h[-1]["w"] = round(h[-1]["w"] + (100 - tot), 4)  # rounding residual of the issuer's 2-decimal table goes to the cash line
    return {"CA:USHI": {"name": "Ninepoint Enhanced U.S. Equity HighShares ETF", "issuer": "ninepoint", "as_of": "2026-09-30", "url": USHI_URL,
                        "source": "Ninepoint Partners website 2026-09-30: portfolio holdings (look-through equity exposure of the 20-25 stock portfolio) + sector table (options, cash)",
                        "coverage": "all 25 stocks the fund holds plus options and cash/borrowing, % of NAV (as published; the table excludes cash/options by itself, the sector table gives them)",
                        "proxy": False, "holdings": h}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("/tmp/gx_pdfs"))
    ap.add_argument("--out", type=Path, default=ROOT / "config" / "holdings_supplements" / "globalx_ir2026.json")
    a = ap.parse_args(argv)
    funds = build(a.cache, GLOBALX_IR2026, BASE, "2026 interim financial statements")
    desc = "Parsed from the PDFs at each entry's url; regenerate with tools/build_supplements.py"
    a.out.write_text(json.dumps({"description": desc, "funds": funds}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", a.out)
    bf = build(a.cache, BROMPTON_IR2026, BROMPTON_FS, "2026 interim financial statements", "brompton", "Brompton Funds", brompton=True)
    bo = a.out.with_name("brompton_ir2026.json")
    bo.write_text(json.dumps({"description": desc, "funds": bf}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", bo)
    so = a.out.with_name("split_corps_2026.json")
    so.write_text(json.dumps({"description": desc, "funds": build_split(a.cache)}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", so)
    uo = a.out.with_name("ninepoint_ushi.json")
    uo.write_text(json.dumps({"description": "Copied from the issuer product page (see url); regenerate with tools/build_supplements.py", "funds": build_ushi()}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", uo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
