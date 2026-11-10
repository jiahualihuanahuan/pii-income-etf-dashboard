"""Pure metric functions: total return (distributions reinvested on the ex-date),
price return, trailing-12-month yield, distribution stability and risk.

All functions take pandas Series indexed by tz-naive dates and never touch the network.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

WINDOWS = ["1m", "3m", "6m", "1y", "3y"]  # "inception" is handled separately


def _offset(window: str) -> pd.DateOffset:
    n = int(window[:-1])
    return pd.DateOffset(months=n) if window.endswith("m") else pd.DateOffset(years=n)


def clean_close(close: pd.Series) -> pd.Series:
    s = close.dropna().astype(float)
    s = s[s > 0]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def clean_divs(divs: pd.Series) -> pd.Series:
    if divs is None or len(divs) == 0:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = divs.dropna().astype(float)
    s = s[s > 0]
    return s[~s.index.duplicated(keep="last")].sort_index()


def window_start(close: pd.Series, end: pd.Timestamp, window: str, slack_days: int = 7) -> Optional[pd.Timestamp]:
    """Trading date used as the window start, or None if history is too short.

    The start is the last trading day on or before (end - window). The series must begin no later
    than `slack_days` after that target, otherwise the window is considered unavailable.
    """
    if len(close) == 0:
        return None
    target = end - _offset(window)
    first = close.index[0]
    if first > target + pd.Timedelta(days=slack_days):
        return None
    prior = close.index[close.index <= target]
    return prior[-1] if len(prior) else first


def total_return(close: pd.Series, divs: pd.Series, start: pd.Timestamp, end: Optional[pd.Timestamp] = None) -> Optional[float]:
    """Cumulative total return from `start` to `end`, reinvesting each distribution at the close
    of its ex-date (a DRIP approximation, the same idea as stockanalysis.com 'total return')."""
    if len(close) == 0 or start is None:
        return None
    end = end or close.index[-1]
    window = close[(close.index >= start) & (close.index <= end)]
    if len(window) < 2:
        return None
    p0, p1 = float(window.iloc[0]), float(window.iloc[-1])
    units = 1.0
    d = clean_divs(divs)
    d = d[(d.index > window.index[0]) & (d.index <= window.index[-1])]
    for ex, amt in d.items():
        # price on the ex-date, or the next trading day if the ex-date is not a trading day
        pos = window.index.searchsorted(ex)
        if pos >= len(window):
            continue
        units *= 1.0 + float(amt) / float(window.iloc[pos])
    return units * p1 / p0 - 1.0


def price_return(close: pd.Series, start: pd.Timestamp, end: Optional[pd.Timestamp] = None) -> Optional[float]:
    if len(close) == 0 or start is None:
        return None
    end = end or close.index[-1]
    window = close[(close.index >= start) & (close.index <= end)]
    if len(window) < 2:
        return None
    return float(window.iloc[-1] / window.iloc[0] - 1.0)


def annualize(tr: Optional[float], days: float) -> Optional[float]:
    if tr is None or days < 365:
        return None
    return (1.0 + tr) ** (365.25 / days) - 1.0


def ttm_yield(close: pd.Series, divs: pd.Series, end: Optional[pd.Timestamp] = None) -> tuple[Optional[float], bool]:
    """Trailing-12-month yield = distributions in the last 365 days / latest close.

    Returns (yield, partial). If the fund has less than ~a year of price history the sum is
    annualised over the history actually available and `partial` is True.
    """
    if len(close) == 0:
        return None, False
    end = end or close.index[-1]
    last = float(close[close.index <= end].iloc[-1])
    d = clean_divs(divs)
    history_days = (end - close.index[0]).days
    start = end - pd.Timedelta(days=365)
    total = float(d[(d.index > start) & (d.index <= end)].sum())
    partial = history_days < 350
    if partial:
        if history_days < 45:
            return None, True
        total = total * 365.0 / history_days
    return total / last, partial


def period_values(d: pd.Series, end: pd.Timestamp) -> tuple[pd.Series, bool]:
    """Distributions bucketed per calendar month (sum of payments in the month; empty months skipped for
    monthly/quarterly payers). Weekly/bi-weekly payers (>1.5 payments per month) get a 3-month rolling mean of the
    monthly sums and the incomplete current month is dropped, so 4-vs-5-week months and noisy payments are not
    mistaken for cuts. Returns (values indexed by month-end, is_weekly)."""
    d = d[d.index <= end]
    if len(d) == 0:
        return pd.Series(dtype=float), False
    m = d.groupby(d.index.to_period("M")).sum()
    span = max((m.index[-1] - m.index[0]).n + 1, 1)
    weekly = len(d) / span > 1.5 and len(d) >= 8
    if weekly:
        if end < end + pd.offsets.MonthEnd(0):  # current month incomplete
            m = m[m.index < end.to_period("M")]
        m = m.reindex(pd.period_range(m.index[0], m.index[-1], freq="M")).fillna(0.0).rolling(3, min_periods=3).mean().dropna()
    m.index = m.index.to_timestamp(how="end").normalize()
    return m, weekly


def distribution_stats(divs: pd.Series, end: pd.Timestamp, cut_threshold: float = 0.10) -> dict:
    """Distribution consistency over the last 12 months, computed on per-month totals (see period_values).

    cut = a period total more than `cut_threshold` below the previous period. severity = sum of the fractional
    size of the cuts. cv = std/mean of the period totals in the window.
    trend = mean(last 3 period totals) / mean(3 before those) - 1."""
    d = clean_divs(divs)
    d = d[d.index <= end]
    out = {"n_payments_12m": 0, "cuts_12m": 0, "cut_severity_12m": 0.0, "cv_12m": None, "trend_pct": None,
           "trend": None, "ttm_growth": None, "last_payment": None, "last_ex_date": None, "frequency": None}
    if len(d) == 0:
        return out
    out["last_payment"] = float(d.iloc[-1])
    out["last_ex_date"] = d.index[-1].strftime("%Y-%m-%d")
    start = end - pd.Timedelta(days=365)
    out["n_payments_12m"] = int((d.index > start).sum())
    pv, weekly = period_values(d, end)
    gaps = d.index.to_series().diff().dt.days.dropna()
    if len(gaps) >= 3:
        g = float(gaps.iloc[-12:].median())
        out["frequency"] = "weekly" if g <= 10 else "monthly" if g <= 40 else "quarterly" if g <= 110 else "semiannual/annual"
    if len(pv) == 0:
        return out
    in_idx = np.where(pv.index > start)[0]
    if len(in_idx) == 0:
        return out
    first_in = in_idx[0]
    seq = pv.iloc[max(first_in - 1, 0):].to_numpy(dtype=float)  # one period of baseline before the window
    cuts, sev = 0, 0.0
    for i in range(1, len(seq)):
        if seq[i - 1] > 0 and seq[i] < seq[i - 1] * (1.0 - cut_threshold):
            cuts += 1
            sev += 1.0 - seq[i] / seq[i - 1]
    out["cuts_12m"], out["cut_severity_12m"] = cuts, round(sev, 4)
    in_win = pv.iloc[first_in:].to_numpy(dtype=float)
    if len(in_win) >= 3 and in_win.mean() > 0:
        out["cv_12m"] = float(in_win.std(ddof=0) / in_win.mean())
    if len(pv) >= 6:
        last3, prev3 = float(pv.iloc[-3:].mean()), float(pv.iloc[-6:-3].mean())
        if prev3 > 0:
            out["trend_pct"] = last3 / prev3 - 1.0
            out["trend"] = "up" if out["trend_pct"] > 0.03 else "down" if out["trend_pct"] < -0.03 else "flat"
    prior = d[(d.index > end - pd.Timedelta(days=730)) & (d.index <= start)]
    if len(prior) >= 3 and (end - d.index[0]).days >= 700 and prior.sum() > 0:
        cur = d[d.index > start].sum()
        out["ttm_growth"] = float(cur / prior.sum() - 1.0)
    return out


def risk_stats(close: pd.Series, divs: pd.Series, end: Optional[pd.Timestamp] = None, days: int = 365) -> dict:
    """Annualised volatility and max drawdown of the total-return index over the last `days`."""
    out = {"vol_1y": None, "maxdd_1y": None}
    if len(close) < 3:
        return out
    end = end or close.index[-1]
    w = close[(close.index > end - pd.Timedelta(days=days)) & (close.index <= end)]
    if len(w) < 60:
        return out
    d = clean_divs(divs).reindex(w.index).fillna(0.0)
    prev = w.shift(1)
    ret = ((w + d) / prev - 1.0).dropna()
    if len(ret) < 59:
        return out
    out["vol_1y"] = float(ret.std(ddof=1) * math.sqrt(252))
    idx = (1.0 + ret).cumprod()
    out["maxdd_1y"] = float((idx / idx.cummax() - 1.0).min())
    return out


def fund_metrics(close: pd.Series, divs: pd.Series, cut_threshold: float = 0.10, as_of: Optional[pd.Timestamp] = None) -> dict:
    """All per-fund metrics in one dict (fractions, not percentages)."""
    close = clean_close(close)
    divs = clean_divs(divs)
    m: dict = {"as_of": None, "last_price": None, "history_days": 0, "tr": {}, "pr": {}, "tr_inception": None,
               "cagr_inception": None, "ttm_yield": None, "ttm_yield_partial": False}
    if len(close) == 0:
        return m
    end = as_of or close.index[-1]
    close = close[close.index <= end]
    m["as_of"] = end.strftime("%Y-%m-%d")
    m["last_price"] = float(close.iloc[-1])
    m["history_days"] = int((end - close.index[0]).days)
    m["inception_date"] = close.index[0].strftime("%Y-%m-%d")
    for w in WINDOWS:
        s = window_start(close, end, w)
        m["tr"][w] = total_return(close, divs, s, end) if s is not None else None
        m["pr"][w] = price_return(close, s, end) if s is not None else None
    m["tr_inception"] = total_return(close, divs, close.index[0], end)
    m["pr_inception"] = price_return(close, close.index[0], end)
    m["cagr_inception"] = annualize(m["tr_inception"], m["history_days"])
    m["ttm_yield"], m["ttm_yield_partial"] = ttm_yield(close, divs, end)
    m.update(distribution_stats(divs, end, cut_threshold))
    m.update(risk_stats(close, divs, end))
    return m


def tr_index(close: pd.Series, divs: pd.Series, base: float = 100.0) -> pd.Series:
    """Total-return index (distributions reinvested on the ex-date), starting at `base`."""
    close = clean_close(close)
    if len(close) == 0:
        return close
    d = clean_divs(divs).reindex(close.index).fillna(0.0)
    r = ((close + d) / close.shift(1)).fillna(1.0)
    return base * r.cumprod()
