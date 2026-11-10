import numpy as np
import pandas as pd

from app import mentions
from app.fetch import split_download

TX = """[0:05] today we talk about HHIS and why it is my favorite.
[0:20] HDIV is the king of total return. Nothing else matters here.
[1:10] I sold ZWC years ago. I would not buy HDIV again at this price."""


def test_find_mentions_timestamps_and_aliases():
    out = mentions.find_mentions(TX, {"HHIS.TO": ["HHIS"], "HDIV.TO": ["HDIV", "HD"], "NOPE": ["NOPE"]})
    assert "NOPE" not in out
    assert out["HHIS.TO"]["snippets"][0]["t"] == 5
    assert out["HDIV.TO"]["n"] == 2
    assert any(s["t"] == 20 for s in out["HDIV.TO"]["snippets"])
    assert mentions.fmt_t(75) == "1:15" and mentions.fmt_t(3725) == "1:02:05"


def test_token_boundaries():
    assert mentions.find_mentions("[0:01] THE HHISX fund", {"X": ["HHIS"]}) == {}


def test_split_download_multiindex_and_missing():
    idx = pd.date_range("2026-01-01", periods=3, tz="America/New_York")
    cols = pd.MultiIndex.from_product([["AAA", "BAD"], ["Close", "Dividends"]])
    df = pd.DataFrame([[10.0, 0.0, np.nan, np.nan], [11.0, 0.5, np.nan, np.nan], [12.0, 0.0, np.nan, np.nan]], index=idx, columns=cols)
    out = split_download(df, ["AAA", "BAD", "MISSING"])
    assert list(out) == ["AAA"]
    close, divs = out["AAA"]
    assert close.index.tz is None and list(divs.values) == [0.5]


def test_common_word_tickers_need_capitals():
    names = {"BANK.TO": ["BANK"], "HHIS.TO": ["HHIS"]}
    assert mentions.find_mentions("[0:01] the bank rates went up and so did the easy money", names) == {}
    assert "BANK.TO" in mentions.find_mentions("[0:01] BANK is Evolve's bank fund", names)
    assert "HHIS.TO" in mentions.find_mentions("[0:01] hhis is great", names)
