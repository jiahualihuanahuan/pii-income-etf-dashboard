"""Holdings: look-through engine, bundle import, API and the xray -> bundle builder (all offline, tiny fixtures)."""
import csv
import gzip
import json

import pytest
from fastapi.testclient import TestClient

from app import db, holdings, holdings_build as hb, holdings_import


def H(name, w, kind="security", ticker="", sub=None, sector="", **kw):
    return {"name": name, "ticker": ticker, "isin": "", "cusip": "", "w": w, "sector": sector, "country": "", "asset_class": "", "currency": "",
            "kind": kind, "sub": sub, **kw}


def fund(key, holdings_, partial=False, disclosed=None, source="issuer site", as_of="2026-10-30", leverage=None, note=None, name=None):
    disclosed = sum(h["w"] for h in holdings_ if h["kind"] != "memo") if disclosed is None else disclosed
    return {"key": key, "market": key.split(":")[0], "ticker": key.split(":")[1], "name": name or key, "issuer": "x", "source": source, "url": "http://x", "as_of": as_of,
            "n_rows": len(holdings_), "raw_sum": 100.0, "coverage": "partial: top 10" if partial else "full", "partial": partial, "disclosed_pct": disclosed,
            "leverage": leverage, "leverage_basis": "holdings" if leverage else None, "note": note, "holdings": holdings_}


BUNDLE = {
    "format": 1, "generated": "2026-11-10T00:00:00", "xray_built": "t", "source_note": "test",
    "funds": {
        # AAA = 50% B (fund of funds) + 50% stock X
        "CA:AAA": fund("CA:AAA", [H("B fund", 50, "fund", sub="CA:BBB"), H("X Corp", 50, ticker="X", sector="Tech")]),
        "CA:BBB": fund("CA:BBB", [H("X Corp", 40, ticker="X"), H("Y Inc", 60, ticker="Y", sector="Energy")]),
        # leveraged: 120% in B, -20% cash
        "CA:LEV": fund("CA:LEV", [H("B fund", 120, "fund", sub="CA:BBB"), H("Cash", -20, "cash")], leverage=1.2),
        # memo rows (underlying top holdings) must not be added
        "CA:MEMO": fund("CA:MEMO", [H("B fund", 99, "fund", sub="CA:BBB"), H("Cash", 1, "cash"), H("X Corp", 40, ticker="X", kind="memo")], note="memo note"),
        # partial: only top 2 disclosed
        "CA:PART": fund("CA:PART", [H("Z", 30, ticker="Z"), H("W", 20, ticker="W")], partial=True),
        # cycle: C1 <-> C2
        "CA:C1": fund("CA:C1", [H("C2", 50, "fund", sub="CA:C2"), H("Q", 50, ticker="Q")]),
        "CA:C2": fund("CA:C2", [H("C1", 50, "fund", sub="CA:C1"), H("R", 50, ticker="R")]),
        # holds a fund we have no data for
        "CA:OPQ": fund("CA:OPQ", [H("Unknown ETF", 40, "fund", sub=None, unlisted_fund="CA:NOPE"), H("X Corp", 60, ticker="X")]),
        "US:SEC": fund("US:SEC", [H("X Corp", 100, ticker="X")], source="SEC EDGAR N-PORT", as_of="2025-01-31"),
    },
    "aliases": {"AAA.TO": "CA:AAA", "LEV.TO": "CA:LEV", "MEMO.TO": "CA:MEMO", "PART.TO": "CA:PART", "C1.TO": "CA:C1", "OPQ.TO": "CA:OPQ", "SEC": "US:SEC",
                "BBB.TO": "CA:BBB"},
}


@pytest.fixture()
def con(env):
    with db.session() as c:
        holdings.import_bundle(BUNDLE, c)
    c = db.connect()
    yield c
    c.close()


def w(payload):
    return {r["name"]: r["weight"] for r in payload["rows"]}


def test_nested_fund_of_funds_multiplies_weights(con):
    p = holdings.etf_payload(con, "AAA.TO")
    assert p["available"] and p["mode"] == "lookthrough"
    ws = w(p)
    assert ws["X Corp"] == pytest.approx(50 + 50 * 0.40)   # direct 50% + 50% x 40%
    assert ws["Y Inc"] == pytest.approx(50 * 0.60)
    assert "B fund" not in ws                               # expanded, not left as a fund row
    assert sum(ws.values()) == pytest.approx(100)
    x = next(r for r in p["rows"] if r["name"] == "X Corp")
    assert x["via"] == {"AAA": pytest.approx(50), "BBB": pytest.approx(20)}
    assert p["summary"]["individual_pct"] == pytest.approx(100) and p["summary"]["complete"] is True
    assert [u["ticker"] for u in p["funds_used"]] == ["AAA", "BBB"]


def test_direct_mode_shows_published_rows_and_flags_expandable(con):
    p = holdings.etf_payload(con, "AAA.TO", lookthrough_mode=False)
    assert p["mode"] == "direct" and [r["name"] for r in p["rows"]] == ["B fund", "X Corp"]
    assert p["rows"][0]["lookthrough"] is True and p["rows"][0]["kind"] == "fund"


def test_negative_cash_is_kept_and_weights_stay_relative_to_nav(con):
    p = holdings.etf_payload(con, "LEV.TO")
    ws = w(p)
    assert ws["Y Inc"] == pytest.approx(120 * 0.6) and ws["Cash & other assets/liabilities"] == pytest.approx(-20)
    assert sum(ws.values()) == pytest.approx(100) and p["summary"]["gross_pct"] == pytest.approx(120)
    assert p["leverage"] == 1.2 and any(f["code"] == "borrowing" for f in p["summary"]["flags"])


def test_memo_rows_are_not_counted_but_visible_in_direct_view(con):
    lt = holdings.etf_payload(con, "MEMO.TO")
    assert w(lt)["X Corp"] == pytest.approx(99 * 0.4)       # only via B, memo X 40% ignored
    d = holdings.etf_payload(con, "MEMO.TO", lookthrough_mode=False)
    assert d["rows"][-1]["kind"] == "memo" and any(f["code"] == "memo_rows" for f in d["summary"]["flags"])


def test_partial_fund_reports_undisclosed_remainder_and_is_not_complete(con):
    p = holdings.etf_payload(con, "PART.TO")
    s = p["summary"]
    assert s["undisclosed_pct"] == pytest.approx(50) and s["complete"] is False
    assert w(p)["Undisclosed remainder of PART (not in source data)"] == pytest.approx(50)
    assert any(f["code"] == "undisclosed" for f in s["flags"])


def test_cycle_does_not_loop_and_is_flagged(con):
    p = holdings.etf_payload(con, "C1.TO")
    assert p["summary"]["complete"] is False and any(f["code"] == "cycle" for f in p["summary"]["flags"])
    assert sum(w(p).values()) == pytest.approx(100)


def test_opaque_fund_without_data_is_kept_and_flagged(con):
    p = holdings.etf_payload(con, "OPQ.TO")
    assert p["summary"]["opaque_fund_pct"] == pytest.approx(40) and p["summary"]["complete"] is False
    assert w(p)["Unknown ETF"] == pytest.approx(40)


def test_nport_stale_flag_and_search_limit(con):
    p = holdings.etf_payload(con, "SEC")
    codes = {f["code"] for f in p["summary"]["flags"]}
    assert {"nport", "stale"} <= codes
    p = holdings.etf_payload(con, "AAA.TO", q="y inc")
    assert [r["name"] for r in p["rows"]] == ["Y Inc"] and p["n_total"] == 1
    assert len(holdings.etf_payload(con, "AAA.TO", limit=1)["rows"]) == 1


def test_missing_etf_is_reported_not_invented(con):
    p = holdings.etf_payload(con, "NOPE.TO")
    assert p["available"] is False


# ---------------------------------------------------------------- API
@pytest.fixture()
def client(env):
    import yaml
    from app.config import paths
    cfgp = paths()["config_dir"] / "tickers.yaml"
    d = yaml.safe_load(cfgp.read_text())
    d["funds"] += [{"ticker": t, "name": t, "listing": "CAD", "category": "a"} for t in ("AAA2.TO",)]
    d["funds"][0]["ticker"] = "AAA.TO"
    cfgp.write_text(yaml.safe_dump(d))
    with db.session() as c:
        holdings.import_bundle(BUNDLE, c)
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_api_holdings_endpoint(client):
    r = client.get("/api/etfs/AAA.TO/holdings?lookthrough=true").json()
    assert r["available"] and r["as_of"] == "2026-10-30" and r["source"] == "issuer site" and r["mode"] == "lookthrough"
    assert {x["name"] for x in r["rows"]} == {"X Corp", "Y Inc"}
    d = client.get("/api/etfs/AAA.TO/holdings?lookthrough=false").json()
    assert {x["name"] for x in d["rows"]} == {"B fund", "X Corp"}
    assert client.get("/api/etfs/NOPE/holdings").status_code == 404
    assert client.get("/api/etfs/AAA2.TO/holdings").json()["available"] is False


def test_api_coverage(client):
    c = client.get("/api/holdings/coverage").json()
    assert c["with_holdings"] == 2 and "AAA2.TO" in c["missing"]
    assert next(i for i in c["items"] if i["ticker"] == "AAA.TO")["complete"] is True


def test_import_replaces_and_cli(env, tmp_path, capsys):
    b = tmp_path / "b.json.gz"
    with gzip.open(b, "wt") as fh:
        json.dump(BUNDLE, fh)
    assert holdings_import.main(["--bundle", str(b)]) == 0
    assert "imported 9 funds" in capsys.readouterr().out
    small = {**BUNDLE, "funds": {"CA:AAA": BUNDLE["funds"]["CA:AAA"]}, "aliases": {"AAA.TO": "CA:AAA"}}
    with db.session() as c:
        holdings.import_bundle(small, c)
        assert c.execute("SELECT COUNT(*) n FROM etf_fund").fetchone()["n"] == 1
        assert c.execute("SELECT COUNT(*) n FROM etf_alias").fetchone()["n"] == 1


def test_auto_import_when_empty_and_bundle_present(env, tmp_path, monkeypatch):
    from app import jobs
    b = tmp_path / "data" / "pii_holdings_bundle.json.gz"
    with gzip.open(b, "wt") as fh:
        json.dump(BUNDLE, fh)
    jobs._auto_import_holdings()
    with db.session() as c:
        assert holdings.has_data(c)


# ---------------------------------------------------------------- builder (xray index -> bundle)
HDR = "m,fund,lvl,name,ticker,isin,cusip,w,sec,cty,ac,ccy,ic,ticker_raw,exchange_country".split(",")


def xray(tmp_path, funds, universe, rows):
    d = tmp_path / "xray"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"built": "t", "holdings_parts": ["holdings_CA.csv.gz", "holdings_US.csv.gz"]}))
    with open(d / "funds.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow("m,fund,issuer,source,url,asof,n,rawsum,coverage,partial,latest_list_type".split(","))
        for f in funds:
            wr.writerow(f)
    with open(d / "universe.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow("m,ticker,base,name,issuer,isin,gap,mer".split(","))
        for u in universe:
            wr.writerow(u)
    for part, m in (("holdings_CA.csv.gz", "CA"), ("holdings_US.csv.gz", "US")):
        with gzip.open(d / part, "wt", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(HDR)
            for r in rows:
                if r[0] == m:
                    wr.writerow(r + [""] * (15 - len(r)))
    return d


def row(m, fund_, name, ticker, w, ac="", isin="", cusip=""):
    return [m, fund_, "D", name, ticker, isin, cusip, str(w), "", "", ac, "", "", ticker, ""]


def test_builder_normalises_scales_memo_leverage_and_resolution(tmp_path):
    F = lambda m, k, raw, partial=0, src="issuer": [m, k, "iss", src, "http://u", "2026-10-30", "9", str(raw), "full", str(partial), ""]  # noqa: E731
    funds = [F("CA", "HYL", 100.1), F("CA", "SMX", 100.0), F("CA", "FRC", 1.0), F("CA", "GXE", 138.41, 1), F("CA", "GXU", 38.0, 1),
             F("CA", "DAY", 199.9), F("CA", "HBF", 100.0), F("US", "QQQ", 100.0)]
    uni = [["CA", "HYL", "HYL", "Hamilton Enhanced U.S. Covered Call ETF", "Hamilton", "", "", ""], ["CA", "SMX", "SMX", "Hamilton US Equity Maximizer ETF", "Hamilton", "", "", ""],
           ["CA", "GXE", "GXE", "Global X Enhanced Cover ETF", "Global X", "", "", ""], ["CA", "GXU", "GXU", "Global X Plain ETF", "Global X", "", "", ""],
           ["CA", "QQQ", "QQQ", "Canadian QQQ lookalike ETF", "Z", "", "", ""]]
    rows = [
        # Hamilton style: sub-fund + negative cash, sums to 100.1, tickers on the fund rows
        row("CA", "HYL", "Hamilton US Equity Maximizer ETF", "SMX", 122.1), row("CA", "HYL", "Cash And Other Assets/Liabilities", "", -22.0),
        row("CA", "SMX", "Apple Inc", "AAPL", 60), row("CA", "SMX", "Microsoft Corp", "MSFT", 40.0),
        # fraction-scale file (sums to 1.0)
        row("CA", "FRC", "Royal Bank", "RY", 0.6, "Equity"), row("CA", "FRC", "TD Bank", "TD", 0.4, "Equity"),
        # Global X: sub-fund "(GXU)" + cash ~100 then the underlying's top holdings -> memo
        row("CA", "GXE", "GLOBAL X PLAIN ETF (GXU)", "", 99.0), row("CA", "GXE", "CASH & CASH EQUIVALENTS", "", 1.0),
        row("CA", "GXE", "APPLE INC", "", 25.0), row("CA", "GXE", "MICROSOFT CORP", "", 13.41),
        row("CA", "GXU", "APPLE INC", "", 20.0), row("CA", "GXU", "MICROSOFT CORP", "", 18.0),
        # Hamilton DayMAX: direct (sub-fund + VOO-like + cash = 100) followed by look-through rows (raw 199.9)
        row("CA", "DAY", "Hamilton US Equity Maximizer ETF", "SMX", 101.0), row("CA", "DAY", "Cash And Other Assets/Liabilities", "", -1.0),
        row("CA", "DAY", "Apple Inc", "AAPL", 60.0), row("CA", "DAY", "Microsoft Corp", "MSFT", 39.9),
        # Harvest: name-only fund row that must match exactly; US QQQ must not be picked for a CA row named QQQ
        row("CA", "HBF", "Hamilton US Equity Maximizer ETF", "", 50), row("CA", "HBF", "Some Real Estate Investment Trust", "SRU.UN", 50),
        row("US", "QQQ", "Apple Inc", "AAPL", 100),
    ]
    d = xray(tmp_path, funds, uni, rows)
    idx = hb.XrayIndex(d)
    rs = idx.rows_for({"CA:HYL", "CA:SMX", "CA:FRC", "CA:GXE", "CA:GXU", "CA:DAY", "CA:HBF", "US:QQQ"})
    b = {k: hb.build_fund(idx, k, v) for k, v in rs.items()}
    hyl = b["CA:HYL"]
    assert hyl["holdings"][0]["sub"] == "CA:SMX" and hyl["holdings"][0]["kind"] == "fund"
    assert hyl["leverage"] == 1.22 and hyl["leverage_basis"] == "holdings"
    assert sum(h["w"] for h in hyl["holdings"]) == pytest.approx(100)                   # normalised to NAV, negative cash kept
    assert b["CA:FRC"]["holdings"][0]["w"] == pytest.approx(60) and b["CA:FRC"]["disclosed_pct"] == pytest.approx(100)  # fractions -> percent
    gx = b["CA:GXE"]
    assert [h["kind"] for h in gx["holdings"]] == ["fund", "cash", "memo", "memo"] and gx["holdings"][0]["sub"] == "CA:GXU"
    assert gx["disclosed_pct"] == pytest.approx(100) and gx["note"]
    day = b["CA:DAY"]
    assert [h["kind"] for h in day["holdings"]].count("memo") == 2 and day["disclosed_pct"] == pytest.approx(100) and day["scale"] == pytest.approx(1.0)
    hbf = b["CA:HBF"]
    assert hbf["holdings"][0]["sub"] == "CA:SMX"           # exact normalised-name match, no fuzzy matching
    assert hbf["holdings"][1]["kind"] == "security"       # a REIT is not mistaken for a fund
    assert hb.dashboard_aliases(["QQQ", "HYL.TO", "HYL-U.TO", "NOPE"], idx) == {"QQQ": "US:QQQ", "HYL.TO": "CA:HYL", "HYL-U.TO": "CA:HYL"}


def test_bundle_roundtrip_writes_gzip(tmp_path):
    p = tmp_path / "x.json.gz"
    hb.write_bundle(BUNDLE, p)
    assert holdings.load_bundle(p)["funds"].keys() == BUNDLE["funds"].keys()
