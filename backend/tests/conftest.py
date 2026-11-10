import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_series(days=900, start_price=20.0, drift=0.0003, vol=0.01, seed=0, end="2026-11-06"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=days)
    px = start_price * np.exp(np.cumsum(rng.normal(drift, vol, len(idx))))
    return pd.Series(px, index=idx)


def monthly_divs(close, amount, every=1, cut_at=None, cut_to=None):
    ex = close.resample("MS").first().index[::every]
    vals = []
    for i, d in enumerate(ex):
        a = amount if cut_at is None or i < cut_at else cut_to
        vals.append(a)
    ex = [close.index[close.index >= d][0] for d in ex if (close.index >= d).any()]
    return pd.Series(vals[: len(ex)], index=pd.DatetimeIndex(ex))


class FakeProvider:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def download(self, tickers, period="max"):
        self.calls.append((list(tickers), period))
        return {t: self.data[t] for t in tickers if t in self.data}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg, seed, data = tmp_path / "config", tmp_path / "seed", tmp_path / "data"
    for p in (cfg, seed, data):
        p.mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setenv("SEED_DIR", str(seed))
    monkeypatch.setenv("DATA_DIR", str(data))
    tickers = {
        "categories": {"a": {"label": "Cat A"}, "b": {"label": "Cat B"}},
        "funds": [
            {"ticker": "AAA.TO", "name": "A fund", "listing": "CAD", "category": "a", "benchmark": "BEN.TO", "aliases": ["AAA"],
             "stances": [{"date": "2025-01-01", "label": "hold", "quote": "old", "video_id": "v1"},
                         {"date": "2026-10-01", "label": "love", "quote": "new", "video_id": "v2"}]},
            {"ticker": "BBB.TO", "name": "B fund", "listing": "CAD", "category": "a", "benchmark": "BEN.TO"},
            {"ticker": "CCC", "name": "C fund", "listing": "USD", "category": "b", "benchmark": "BEN.TO"},
            {"ticker": "DDD.TO", "name": "D fund", "listing": "CAD", "category": "a", "benchmark": "BEN.TO"},
            {"ticker": "BEN.TO", "name": "Benchmark", "listing": "CAD", "category": "a", "role": "benchmark"},
        ],
        "unresolved": [],
    }
    (cfg / "tickers.yaml").write_text(yaml.safe_dump(tickers))
    (seed / "videos.json").write_text(json.dumps([
        {"id": "v1", "title": "Old video", "date": "2025-01-01", "views": 500000},
        {"id": "v2", "title": "New video", "date": "2026-11-01", "views": 20000, "has_transcript": True},
    ]))
    (seed / "mentions.json").write_text(json.dumps({"AAA": [{"video_id": "v2", "date": "2026-11-01", "t": 75, "snippet": "AAA is great."}]}))
    (seed / "mindset.json").write_text(json.dumps({"commandments": [{"n": 1, "title": "x"}]}))
    (cfg / "settings.yaml").write_text(yaml.safe_dump({"scheduler_enabled": False, "bootstrap_on_start": False}))
    from app import db
    db.init()
    return tmp_path


@pytest.fixture()
def provider():
    d = {}
    for i, (t, amt, drift) in enumerate([("AAA.TO", 0.12, 0.0004), ("BBB.TO", 0.10, 0.0001), ("CCC", 0.20, -0.0002),
                                         ("DDD.TO", 0.15, 0.0003), ("BEN.TO", 0.05, 0.0003)]):
        c = make_series(seed=i, drift=drift)
        d[t] = (c, monthly_divs(c, amt, cut_at=(30 if t == "CCC" else None), cut_to=amt * 0.5))
    return FakeProvider(d)
