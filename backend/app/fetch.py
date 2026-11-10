"""Market-data download via yfinance (unofficial, no API key). Network code lives here only."""
from __future__ import annotations

import logging
import time
from typing import Callable, Iterable

import pandas as pd

log = logging.getLogger("fetch")


def _naive(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def split_download(df: pd.DataFrame, tickers: list[str]) -> dict[str, tuple[pd.Series, pd.Series]]:
    """Split a yf.download(group_by='ticker', actions=True, auto_adjust=False) frame into {ticker: (close, dividends)}."""
    out: dict[str, tuple[pd.Series, pd.Series]] = {}
    if df is None or len(df) == 0:
        return out
    multi = isinstance(df.columns, pd.MultiIndex)
    for t in tickers:
        try:
            sub = df[t] if multi else df
        except KeyError:
            continue
        if "Close" not in sub.columns:
            continue
        close = sub["Close"].dropna()
        if len(close) == 0:
            continue
        close.index = _naive(close.index)
        divs = sub["Dividends"] if "Dividends" in sub.columns else pd.Series(dtype=float)
        divs = divs.fillna(0.0)
        divs = divs[divs > 0]
        divs.index = _naive(divs.index)
        out[t] = (close, divs)
    return out


class YahooProvider:
    """download(tickers, period) -> {ticker: (close, dividends)}; chunked, with retry/backoff for rate limits."""

    def __init__(self, chunk_size: int = 40, pause: float = 2.0, retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.chunk_size, self.pause, self.retries, self.sleep = chunk_size, pause, retries, sleep

    def download(self, tickers: list[str], period: str = "max") -> dict[str, tuple[pd.Series, pd.Series]]:
        import yfinance as yf  # imported lazily so tests never need the network layer
        out: dict[str, tuple[pd.Series, pd.Series]] = {}
        for i in range(0, len(tickers), self.chunk_size):
            chunk = tickers[i:i + self.chunk_size]
            for attempt in range(self.retries):
                try:
                    df = yf.download(chunk, period=period, interval="1d", auto_adjust=False, actions=True,
                                     group_by="ticker", threads=False, progress=False)
                    got = split_download(df, chunk)
                    out.update(got)
                    if got or attempt == self.retries - 1:
                        break
                except Exception as e:  # rate limit etc.
                    log.warning("yfinance chunk failed (%s), attempt %d", e, attempt + 1)
                self.sleep(self.pause * (3 ** attempt) * 5)
            self.sleep(self.pause)
        # brand-new listings can reject period="max"; retry whatever is missing with a short period
        missing = [t for t in tickers if t not in out]
        if missing and period not in ("1d", "5d", "1mo"):
            try:
                df = yf.download(missing, period="1mo", interval="1d", auto_adjust=False, actions=True,
                                 group_by="ticker", threads=False, progress=False)
                out.update(split_download(df, missing))
            except Exception as e:
                log.warning("fallback download failed: %s", e)
        return out
