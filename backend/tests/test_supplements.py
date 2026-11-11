"""Financial-statement schedules, supplement funds and tail proxies (offline, tiny fixtures)."""
import json

import pytest

from app import db, holdings, holdings_build as hb, schedule_parser as sp
from tests.test_holdings import row, xray

GX = """
Global X Test Covered Call ETF

Schedule of Investments (unaudited)
As at June 30, 2026
                                                    Shares/        Average           Fair
    Security                                       Contracts        Cost            Value
    CANADIAN EQUITIES (60.00%)
    Materials (60.00%)
       Agnico Eagle Mines Ltd.                      1,000 $     500,000 $        600,000
                                                              500,000          600,000

    TOTAL CANADIAN EQUITIES                                       500,000          600,000

    GLOBAL EQUITIES (40.00%)
    United Kingdom (40.00%)
       AngloGold Ashanti PLC, ADR                      2,000      300,000          400,000
    TOTAL GLOBAL EQUITIES                                         300,000          400,000

    DERIVATIVES (-1.00%)
    SHORT POSITIONS (-1.00%)
    Call Options (-1.00%)
       Agnico Eagle, July 2026, $200.00 CAD             (10)      (5,000)          (10,000)
    TOTAL DERIVATIVES                                              (5,000)         (10,000)

       Transaction Costs                                              (100)

    TOTAL INVESTMENT PORTFOLIO (99.00%)                    $   795,000 $        990,000
    Cash and cash equivalents (1.50%)                                                    15,000
    Other assets less liabilities (-0.50%)                                               (5,000)
    NET ASSETS (100.00%)                                                       $   1,000,000

Notes to Financial Statements (unaudited)
"""

BR = """
Brompton Test Income ETF - Interim Financial Statements 2026
SCHEDULE OF INVESTMENT PORTFOLIO (Unaudited)
As at June 30, 2026

                                         Number of                        Cost3     Carrying Value              % of Net
                                           Units                           $                    $                Assets
    Investment Funds1
       Brompton Alpha ETF                    1,000              9,000             10,000
       Brompton Flaherty & Crumrine Investment Grade       2,000             18,000             20,000
       Preferred ETF2
       Par Capital Corp., 5.000% due       $   1,000                 900                30
       March 30, 2062
    Total Investment Funds                                                     27,000             30,030          120.0
    Total Investments                                                          27,000             30,030          120.0
    Cash                                                                                       10                0.0
    Loans payable (note 8)                                                                  (5,000)          (20.0)
    Other net assets (liabilities)                                                              (40)          (0.0)
    Net Assets attributable to holders of redeemable units                                  25,000             100.0
"""


def test_globalx_schedule_weights_add_to_100_and_keep_options_and_cash():
    p = sp.parse_schedule(GX)
    assert p["as_of"] == "2026-06-30" and p["net_assets"] == 1_000_000
    h = sp.to_holdings(p)
    assert sum(x["w"] for x in h) == pytest.approx(100)
    by = {x["name"]: x for x in h}
    assert by["Agnico Eagle Mines Ltd."]["w"] == pytest.approx(60) and by["Agnico Eagle Mines Ltd."]["sector"] == "Materials"
    assert by["Agnico Eagle Mines Ltd."]["country"] == "Canada"
    assert by["AngloGold Ashanti PLC, ADR"]["country"] == "United Kingdom" and by["AngloGold Ashanti PLC, ADR"]["sector"] == ""
    opt = by["Agnico Eagle, July 2026, $200.00 CAD"]
    assert opt["kind"] == "derivative" and opt["w"] == pytest.approx(-1.0)
    assert by["Cash and cash equivalents"]["kind"] == "cash" and by["Other assets less liabilities"]["w"] == pytest.approx(-0.5)


def test_brompton_schedule_wrapped_names_and_loans():
    p = sp.parse_brompton_schedule(BR, "Brompton Test Income ETF")
    names = [r["name"] for r in p["rows"]]
    assert names == ["Brompton Alpha ETF", "Brompton Flaherty & Crumrine Investment Grade Preferred ETF", "Par Capital Corp., 5.000% due March 30, 2062"]
    assert p["net_assets"] == 25_000 and p["check"]["total_pct"] == pytest.approx(100)
    assert p["cash"]["Loans payable"] == pytest.approx(-20.0)
    assert p["rows"][0]["w"] == pytest.approx(40.0)
    h = sp.to_holdings(p)
    assert [x["kind"] for x in h[:2]] == ["fund", "fund"] and h[2]["kind"] == "security"


def _mini(tmp_path, supp):
    F = lambda m, k, raw, partial=0: [m, k, "iss", "issuer", "http://u", "2026-10-30", "9", str(raw), "full", str(partial), ""]  # noqa: E731
    funds = [F("CA", "TOP", 100), F("CA", "IDX", 70, 1), F("US", "SIS", 100)]
    uni = [["CA", "TOP", "TOP", "Test Covered Call ETF", "Test", "", "", ""], ["CA", "IDX", "IDX", "Test Index ETF", "Test", "", "", ""],
           ["US", "SIS", "SIS", "Sister ETF", "Test", "", "", ""]]
    rows = [row("CA", "TOP", "Test Index ETF", "IDX", 100),
            row("CA", "IDX", "Alpha Mining Corp", "", 40), row("CA", "IDX", "Beta Silver Inc", "", 30),
            row("US", "SIS", "ALPHA MINING CORPORATION", "ALP", 35, "Equity", isin="US0000000001", cusip="000000001"), row("US", "SIS", "Gamma Metals Ltd", "GAM", 25, "Equity"),
            row("US", "SIS", "Delta Metals Ltd", "DEL", 15, "Equity"), row("US", "SIS", "Treasury Bill", "", 20, "Fixed income"), row("US", "SIS", "Repo Co Securities Inc", "", 5, "Repurchase")]
    d = xray(tmp_path, funds, uni, rows)
    sd = tmp_path / "supp"
    sd.mkdir()
    (sd / "s.json").write_text(json.dumps({"funds": supp}))
    y = tmp_path / "tickers.yaml"
    y.write_text("funds:\n  - {ticker: TOP.TO}\n")
    return hb.build_bundle(d, y, log=lambda *a: None, supplements_dir=sd)


def test_tail_proxy_keeps_published_rows_and_fills_only_the_rest(tmp_path):
    b = _mini(tmp_path, {"CA:IDX": {"mode": "tail_proxy", "proxy_of": "US:SIS", "name": "Test Index ETF", "source": "PROXY (remainder only): SIS",
                                    "name_aliases": {"Alpha Mining Corp": "ALPHA MINING CORPORATION"}}})
    f = b["funds"]["CA:IDX"]
    assert f["proxy"] is True and f["source"].startswith("PROXY") and f["partial"] is False
    ws = {h["name"]: h["w"] for h in f["holdings"]}
    assert ws["Alpha Mining Corp"] == 40 and ws["Beta Silver Inc"] == 30            # published weights untouched, no double count of the aliased name
    assert "ALPHA MINING CORPORATION" not in ws
    assert sum(ws.values()) == pytest.approx(100)
    assert ws["Gamma Metals Ltd"] / ws["Delta Metals Ltd"] == pytest.approx(25 / 15)  # filler keeps the sister fund's relative weights
    assert set(ws) == {"Alpha Mining Corp", "Beta Silver Inc", "Gamma Metals Ltd", "Delta Metals Ltd"}  # T-bills / repo are not stock filler
    assert "ESTIMATED" in f["note"] and f["replaces"] == "issuer"


def test_supplement_replaces_partial_fund_ids_backfill_and_proxy_flag(tmp_path, env):
    supp = {"CA:IDX": {"name": "Test Index ETF", "issuer": "test", "as_of": "2026-06-30", "url": "http://pdf", "source": "Test interim statements",
                       "holdings": [{"name": "Alpha Mining Corp", "w": 60.0, "kind": "security"}, {"name": "Zeta Corp", "w": 39.0, "kind": "security"},
                                    {"name": "Cash", "w": 1.0, "kind": "cash"}]}}
    b = _mini(tmp_path, supp)
    f = b["funds"]["CA:IDX"]
    assert f["partial"] is False and f["as_of"] == "2026-06-30" and f["disclosed_pct"] == pytest.approx(100)
    alpha = f["holdings"][0]
    assert alpha["ticker"] == "ALP" and alpha["cusip"] == "000000001"          # ids learned from the index by exact normalised name
    assert f["holdings"][1]["ticker"] == ""                                      # unknown names get nothing guessed
    top = b["funds"]["CA:TOP"]
    assert top["holdings"][0]["sub"] == "CA:IDX"
    # proxy flag surfaces in the look-through payload
    b["funds"]["CA:IDX"]["source"] = "PROXY: demo"
    with db.session() as c:
        holdings.import_bundle(b, c)
    c = db.connect()
    p = holdings.etf_payload(c, "TOP.TO")
    c.close()
    assert p["summary"]["complete"] is True and "proxy" in [x["code"] for x in p["summary"]["flags"]]
