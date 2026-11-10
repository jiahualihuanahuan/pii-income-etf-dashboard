"""Sanity checks on the shipped config/ and seed/ files (no network)."""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
UNI = yaml.safe_load((ROOT / "config" / "tickers.yaml").read_text())
FUNDS = {f["ticker"]: f for f in UNI["funds"]}


def listing(sym):
    return "CAD" if sym.endswith(".TO") and not sym.endswith("-U.TO") else "USD"


def test_unique_tickers_and_listing_matches_symbol_suffix():
    assert len(FUNDS) == len(UNI["funds"])
    for t, f in FUNDS.items():
        assert f["listing"] == listing(t), t
        assert f["role"] in ("fund", "benchmark")
        assert f["category"] in UNI["categories"], (t, f["category"])


def test_benchmark_exists_is_a_benchmark_and_has_same_currency():
    for t, f in FUNDS.items():
        b = f.get("benchmark")
        if not b:
            continue
        assert b in FUNDS, (t, b)
        assert FUNDS[b]["role"] == "benchmark", (t, b)
        assert FUNDS[b]["listing"] == f["listing"], f"{t} ({f['listing']}) vs benchmark {b} ({FUNDS[b]['listing']}): would measure FX"


def test_pairs_are_symmetric_and_cross_listed():
    for t, f in FUNDS.items():
        p = f.get("pair")
        if p:
            assert FUNDS[p]["pair"] == t
            assert FUNDS[p]["listing"] != f["listing"]


def test_stances_are_dated_labelled_and_linked():
    n = 0
    for f in UNI["funds"]:
        for s in f.get("stances", []):
            n += 1
            assert len(str(s["date"])) == 10 and s["label"] and s["url"].startswith("https://youtu.be/")
            assert "quote" in s or "summary" in s
    assert n >= 20


def test_both_listings_present_and_unresolved_documented():
    funds = [f for f in UNI["funds"] if f["role"] == "fund"]
    assert sum(f["listing"] == "CAD" for f in funds) > 50 and sum(f["listing"] == "USD" for f in funds) > 20
    assert all(u.get("note") for u in UNI["unresolved"])
    assert all(e.get("reason") for e in UNI["excluded"])


def test_seed_files_load_and_are_small():
    videos = json.loads((ROOT / "seed" / "videos.json").read_text())
    mentions = json.loads((ROOT / "seed" / "mentions.json").read_text())
    mindset = json.loads((ROOT / "seed" / "mindset.json").read_text())
    assert len(videos) > 800 and all("id" in v and "title" in v for v in videos)
    assert len(mindset["commandments"]) == 10 and mindset["checklist"]
    for fn in ("videos.json", "mentions.json", "mindset.json"):
        assert (ROOT / "seed" / fn).stat().st_size < 1_500_000, fn
    assert all(len(m["snippet"]) <= 320 for items in mentions.values() for m in items)


def test_no_secrets_or_dumps_committed():
    for p in ROOT.rglob("*"):
        if p.is_file() and not any(x in p.parts for x in (".venv", "node_modules", ".git", "dist", "data", ".pytest_cache", "__pycache__")):
            assert p.name != ".env", p
            assert p.stat().st_size < 2_000_000, p
