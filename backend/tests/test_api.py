import json

import pytest
from fastapi.testclient import TestClient

from app import db, jobs, seed
from conftest import FakeProvider


@pytest.fixture()
def client(env, provider):
    jobs.bootstrap()
    jobs.run_job("full", provider=provider)
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health_and_status(client):
    assert client.get("/api/health").json() == {"ok": True}
    s = client.get("/api/status").json()
    assert s["funds"] == 5 and s["funds_with_prices"] == 5 and s["jobs"][0]["status"] == "ok"


def test_funds_ranked_and_benchmarks_hidden(client):
    r = client.get("/api/funds").json()
    tickers = [x["ticker"] for x in r["rows"]]
    assert "BEN.TO" not in tickers and len(tickers) == 4
    assert [x["rank"] for x in r["rows"]] == [1, 2, 3, 4]
    assert abs(sum(r["settings"]["weights"].values()) - 1) < 1e-9
    assert client.get("/api/funds?benchmarks=true").json()["rows"][-1]["ticker"] == "BEN.TO"
    row = r["rows"][0]
    assert row["vs_benchmark"]["1y"] is not None and row["metrics"]["ttm_yield"] > 0


def test_query_weights_change_ranking_and_filters(client):
    a = client.get("/api/funds?w_total_return=1&w_stability=0&w_yield=0").json()["rows"]
    b = client.get("/api/funds?w_total_return=0&w_stability=0&w_yield=1").json()["rows"]
    assert [x["ticker"] for x in a] != [x["ticker"] for x in b]
    assert {x["ticker"] for x in client.get("/api/funds?listing=USD").json()["rows"]} == {"CCC"}
    assert {x["ticker"] for x in client.get("/api/funds?category=a").json()["rows"]} == {"AAA.TO", "BBB.TO", "DDD.TO"}
    assert client.get("/api/funds?window=3y&scope=category").status_code == 200


def test_stance_newest_wins_and_mention(client):
    row = next(x for x in client.get("/api/funds").json()["rows"] if x["ticker"] == "AAA.TO")
    assert row["stance"]["label"] == "love" and len(row["stance"]["history"]) == 1
    assert row["stance"]["url"] == "https://youtu.be/v2"
    assert row["latest_mention"]["url"] == "https://youtu.be/v2?t=75"
    st = client.get("/api/stances").json()
    assert st[0]["ticker"] == "AAA.TO"


def test_settings_roundtrip_and_validation(client, env):
    r = client.put("/api/settings", json={"weights": {"total_return": 1, "stability": 1, "yield": 2}, "half_life_days": 90}).json()["settings"]
    assert r["weights"]["yield"] == pytest.approx(0.5) and r["half_life_days"] == 90
    assert (env / "data" / "settings.override.yaml").exists()
    assert client.get("/api/funds").json()["settings"]["weights"]["yield"] == pytest.approx(0.5)
    assert client.put("/api/settings", json={"window": "9y"}).status_code == 422
    assert client.put("/api/settings", json={"weights": {"yield": -1}}).status_code == 422
    r = client.delete("/api/settings").json()["settings"]
    assert r["weights"]["total_return"] == pytest.approx(0.45)


def test_distributions_history_videos_mindset(client):
    d = client.get("/api/funds/CCC/distributions?years=5").json()["items"]
    assert any(x["cut"] for x in d)  # CCC has a built-in 50% cut
    h = client.get("/api/funds/AAA.TO/history?years=1").json()["items"]
    assert h[0]["total_return"] == pytest.approx(100.0) and len(h) > 200
    v = client.get("/api/videos").json()
    assert v[0]["id"] == "v2"  # recent video beats the older, more-viewed one
    assert client.get("/api/videos?half_life_days=100000&views_transform=linear").json()[0]["id"] == "v1"
    assert client.get("/api/mindset").json()["commandments"][0]["n"] == 1
    assert client.get("/api/categories").json()[0]["count"] == 3
    assert client.get("/api/funds/NOPE").status_code == 404
    det = client.get("/api/funds/AAA.TO").json()
    assert det["mentions"][0]["video_id"] == "v2"


def test_prices_job_is_incremental_and_unknown_job(client, provider):
    n = len(provider.calls)
    jobs.run_job("prices", provider=provider)
    assert provider.calls[-1][1] == "5d" and len(provider.calls) == n + 1
    assert client.post("/api/jobs/nope/run").status_code == 404
    assert client.post("/api/jobs/prices/run").json() == {"started": "prices"}


def test_seed_is_idempotent(env):
    a = seed.load()
    b = seed.load()
    assert a["videos_added"] == 2 and b["videos_added"] == 0 and b["mentions_added"] == 0
