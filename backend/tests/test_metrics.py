import numpy as np
import pandas as pd
import pytest

from app import metrics
from conftest import make_series, monthly_divs


def idx(*dates):
    return pd.DatetimeIndex(dates)


def test_total_return_reinvests_dividend_on_exdate():
    close = pd.Series([100.0, 100.0, 50.0, 100.0], index=idx("2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"))
    divs = pd.Series([5.0], index=idx("2026-01-05"))  # 5/50 = 10% reinvested at 50
    tr = metrics.total_return(close, divs, close.index[0])
    assert tr == pytest.approx(1.10 * 1.0 - 1.0, rel=1e-9)  # units 1.1, end price 100 vs start 100
    assert metrics.price_return(close, close.index[0]) == pytest.approx(0.0)


def test_total_return_ignores_dividend_on_start_day_and_after_end():
    close = pd.Series([10.0, 11.0, 12.0], index=idx("2026-01-01", "2026-01-02", "2026-01-03"))
    divs = pd.Series([1.0, 1.0], index=idx("2026-01-01", "2026-02-01"))
    assert metrics.total_return(close, divs, close.index[0]) == pytest.approx(0.2)


def test_total_return_dividend_on_non_trading_day_uses_next_close():
    close = pd.Series([10.0, 10.0, 20.0], index=idx("2026-01-02", "2026-01-05", "2026-01-06"))
    divs = pd.Series([1.0], index=idx("2026-01-03"))  # Saturday -> next trading day Jan 5 at 10
    assert metrics.total_return(close, divs, close.index[0]) == pytest.approx(1.1 * 2.0 - 1.0)


def test_window_start_requires_enough_history():
    c = make_series(days=100)  # ~5 months
    assert metrics.window_start(c, c.index[-1], "1y") is None
    assert metrics.window_start(c, c.index[-1], "3m") is not None


def test_ttm_yield_full_year_and_partial():
    c = make_series(days=600, start_price=10.0, drift=0, vol=0.0001)
    d = monthly_divs(c, 0.1)
    y, partial = metrics.ttm_yield(c, d)
    assert not partial
    assert y == pytest.approx(1.2 / float(c.iloc[-1]), rel=0.15)  # 12 payments of 0.1 (11 or 12 in window)
    short = c.iloc[-120:]
    y2, partial2 = metrics.ttm_yield(short, d)
    assert partial2 and y2 is not None and y2 > 0
    y3, p3 = metrics.ttm_yield(c.iloc[-20:], d)
    assert y3 is None


def test_distribution_stats_detects_cut_and_trend():
    c = make_series(days=700)
    d = monthly_divs(c, 0.2, cut_at=len(monthly_divs(c, 0.2)) - 4, cut_to=0.1)
    st = metrics.distribution_stats(d, c.index[-1], 0.10)
    assert st["cuts_12m"] == 1
    assert st["cut_severity_12m"] == pytest.approx(0.5, abs=0.01)
    assert st["trend"] == "down"
    steady = metrics.distribution_stats(monthly_divs(c, 0.2), c.index[-1])
    assert steady["cuts_12m"] == 0 and steady["cv_12m"] == pytest.approx(0.0, abs=1e-9) and steady["trend"] == "flat"


def test_small_decrease_is_not_a_cut():
    c = make_series(days=500)
    d = pd.Series([0.20, 0.19, 0.20, 0.19, 0.2], index=c.index[[-120, -90, -60, -30, -5]])
    assert metrics.distribution_stats(d, c.index[-1], 0.10)["cuts_12m"] == 0


def test_risk_stats_drawdown_negative_and_vol_positive():
    c = make_series(days=400, vol=0.02)
    r = metrics.risk_stats(c, pd.Series(dtype=float))
    assert r["vol_1y"] > 0.1 and r["maxdd_1y"] < 0
    assert metrics.risk_stats(c.iloc[:30], pd.Series(dtype=float))["vol_1y"] is None


def test_fund_metrics_end_to_end_and_tr_index_consistent():
    c = make_series(days=1000, seed=3)
    d = monthly_divs(c, 0.1)
    m = metrics.fund_metrics(c, d)
    assert set(m["tr"]) == set(metrics.WINDOWS)
    assert m["tr"]["3y"] is not None
    # inception TR equals the final value of the TR index / 100 - 1
    assert metrics.tr_index(c, d).iloc[-1] / 100 - 1 == pytest.approx(m["tr_inception"], rel=1e-6)
    assert m["tr_inception"] > m["pr_inception"]  # dividends add return
    assert metrics.fund_metrics(pd.Series(dtype=float), pd.Series(dtype=float))["as_of"] is None


def test_weekly_noisy_payer_without_cuts_shows_no_cuts():
    # weekly payer whose payments fluctuate +/-30% around a constant level; 4-vs-5 payment months. Must not be flagged.
    rng = np.random.default_rng(7)
    c = make_series(days=900)
    weekly_idx = c.index[c.index.day_of_week == 4]
    d = pd.Series(0.3 * rng.uniform(0.7, 1.3, len(weekly_idx)), index=weekly_idx)
    st = metrics.distribution_stats(d, c.index[-1], 0.10)
    assert st["frequency"] == "weekly"
    assert st["cuts_12m"] == 0
    assert st["cv_12m"] is not None and st["cv_12m"] < 0.15


def test_weekly_payer_real_cut_is_detected():
    c = make_series(days=900)
    wk = c.index[c.index.day_of_week == 4]
    amounts = [0.3 if i < len(wk) - 20 else 0.15 for i in range(len(wk))]
    d = pd.Series(amounts, index=wk)
    st = metrics.distribution_stats(d, c.index[-1], 0.10)
    assert st["cuts_12m"] >= 1 and st["trend"] == "down"


def test_quarterly_payer_not_flagged_for_empty_months():
    c = make_series(days=900)
    d = monthly_divs(c, 1.0, every=3)
    st = metrics.distribution_stats(d, c.index[-1])
    assert st["cuts_12m"] == 0 and st["frequency"] == "quarterly"


def test_fund_with_no_dividends_and_with_empty_series_does_not_crash():
    c = make_series(days=500)
    m = metrics.fund_metrics(c, pd.Series(dtype=float))
    assert m["ttm_yield"] == 0.0 and m["tr_inception"] == pytest.approx(m["pr_inception"]) and m["cuts_12m"] == 0
    assert metrics.tr_index(c, pd.Series(dtype=float)).iloc[-1] == pytest.approx(100 * c.iloc[-1] / c.iloc[0])
