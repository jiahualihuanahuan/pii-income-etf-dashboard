"""Broad groups: every category maps to a group, tech/sector/commodity splits, API filters."""
import pytest

from app import groups


def test_every_category_in_repo_config_maps_to_a_known_group():
    import yaml
    from pathlib import Path
    uni = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "tickers.yaml").read_text())
    assert set(uni["categories"]) <= set(groups.CATEGORY_GROUP)
    assert set(groups.CATEGORY_GROUP.values()) <= set(groups.GROUPS)
    for f in uni["funds"]:
        g, s = groups.classify({**f, "group": None, "subsector": None}, None)
        assert f["group"] == g and f["group"] in uni["groups"], f["ticker"]       # tickers.yaml is up to date (tools/assign_groups.py)
        assert (f["group"] != "sector") or f.get("subsector"), f["ticker"]
        assert f["category"]                                                       # detailed category is kept as the secondary field


@pytest.mark.parametrize("cat,name,expect", [
    ("nasdaq", "Hamilton Technology YIELD MAXIMIZER ETF", ("sector", "Tech")),
    ("nasdaq", "Global X NASDAQ-100 Covered Call ETF", ("diversified", None)),
    ("nasdaq", "REX AI Equity Premium Income ETF", ("sector", "Tech")),
    ("sector", "BMO Covered Call Utilities ETF", ("sector", "Utilities")),
    ("sector", "CI Health Care Giants Covered Call ETF", ("sector", "Healthcare")),
    ("sector", "Harvest REIT Leaders Income ETF", ("sector", "Real estate")),
    ("sector", "Brompton Global Infrastructure ETF", ("sector", "Infrastructure")),
    ("energy", "Hamilton Energy YIELD MAXIMIZER ETF", ("sector", "Energy")),
    ("financials", "BMO Covered Call Canadian Banks ETF", ("sector", "Financials")),
    ("gold", "Global X Silver Covered Call ETF", ("commodities", "Silver")),
    ("gold", "Hamilton Gold Producer YIELD MAXIMIZER ETF", ("commodities", "Gold")),
    ("bitcoin", "Roundhill Bitcoin Covered Call Strategy ETF", ("crypto", "Bitcoin")),
    ("bitcoin", "Harvest Strategy Inc. High Income Shares ETF", ("crypto", "Bitcoin-proxy stock (MSTR)")),
    ("single_stock", "YieldMax Universe Fund of Option Income ETFs", ("single_stock", None)),
    ("sp500", "JPMorgan Equity Premium Income ETF", ("diversified", None)),
    ("split", "Financial 15 Split Corp.", ("other", "Financials")),
    ("unknown-cat", "whatever", ("other", None)),
])
def test_classify(cat, name, expect):
    assert groups.classify({"category": cat, "name": name}) == expect


def test_explicit_group_wins():
    assert groups.classify({"category": "sp500", "name": "x", "group": "single_stock"}) == ("single_stock", None)


def test_api_groups_filter_and_category_kept(env, provider):
    from fastapi.testclient import TestClient
    from app import jobs
    import yaml
    from app.config import paths
    p = paths()["config_dir"] / "tickers.yaml"
    d = yaml.safe_load(p.read_text())
    d["funds"][0].update(group="sector", subsector="Tech")      # AAA.TO
    d["funds"][2].update(group="crypto")                         # CCC
    p.write_text(yaml.safe_dump(d))
    jobs.bootstrap()
    jobs.run_job("full", provider=provider)
    from app.main import app
    with TestClient(app) as c:
        rows = c.get("/api/funds").json()["rows"]
        assert {r["ticker"]: (r["group"], r["category"]) for r in rows}["AAA.TO"] == ("sector", "a")
        assert {r["ticker"] for r in c.get("/api/funds?group=sector").json()["rows"]} == {"AAA.TO"}
        assert {r["ticker"] for r in c.get("/api/funds?group=sector&subsector=tech").json()["rows"]} == {"AAA.TO"}
        gs = {g["key"]: g for g in c.get("/api/groups").json()}
        assert gs["sector"]["subsectors"] == {"Tech": 1} and gs["crypto"]["count"] == 1 and gs["sector"]["categories"] == {"a": 1}
        assert c.get("/api/funds?scope=category&window=1y").status_code == 200
