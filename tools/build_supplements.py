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
    return 0


if __name__ == "__main__":
    sys.exit(main())
