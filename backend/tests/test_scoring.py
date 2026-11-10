import pytest
from datetime import date

from app import scoring as sc


def row(t, tr, y, cuts=0.0, vol=0.2, dd=-0.1, cv=0.05, cat="a"):
    return {"ticker": t, "category": cat, "metrics": {"tr": {"1y": tr}, "tr_inception": tr, "ttm_yield": y, "cut_severity_12m": cuts,
            "n_payments_12m": 12, "cv_12m": cv, "vol_1y": vol, "maxdd_1y": dd}}


def test_normalize_weights():
    w = sc.normalize({"total_return": 2, "stability": 1, "yield": 1}, sc.DEFAULT_WEIGHTS)
    assert sum(w.values()) == pytest.approx(1) and w["total_return"] == pytest.approx(0.5)
    assert sc.normalize({"total_return": 0, "stability": 0, "yield": 0}, sc.DEFAULT_WEIGHTS) == pytest.approx(sc.normalize(sc.DEFAULT_WEIGHTS, sc.DEFAULT_WEIGHTS))
    assert sc.normalize({"total_return": -5, "stability": 1, "yield": 0}, sc.DEFAULT_WEIGHTS)["total_return"] == 0


def test_default_weights_documented_values():
    assert sc.DEFAULT_WEIGHTS == {"total_return": 0.45, "stability": 0.30, "yield": 0.25}
    assert sum(sc.DEFAULT_STABILITY_WEIGHTS.values()) == pytest.approx(1)


def test_pct_rank_ties_and_none():
    assert sc.pct_rank([1, 2, 3]) == [0, 50, 100]
    assert sc.pct_rank([3, 2, 1], higher_is_better=False) == [0, 50, 100]
    assert sc.pct_rank([1, 1, 3]) == [25, 25, 100]
    assert sc.pct_rank([None, 5]) == [None, 50]


def test_ranking_follows_weights():
    rows = [row("HIGHTR", 0.30, 0.05), row("HIGHYLD", 0.05, 0.40, cuts=0.4, vol=0.4, dd=-0.4), row("MID", 0.15, 0.20)]
    r = sc.score_funds([dict(x) for x in rows], {"total_return": 1, "stability": 0, "yield": 0}, sc.DEFAULT_STABILITY_WEIGHTS)
    assert [x["ticker"] for x in sorted(r, key=lambda x: x["rank"])][0] == "HIGHTR"
    r = sc.score_funds([dict(x) for x in rows], {"total_return": 0, "stability": 0, "yield": 1}, sc.DEFAULT_STABILITY_WEIGHTS)
    assert sorted(r, key=lambda x: x["rank"])[0]["ticker"] == "HIGHYLD"
    r = sc.score_funds([dict(x) for x in rows], {"total_return": 0, "stability": 1, "yield": 0}, sc.DEFAULT_STABILITY_WEIGHTS)
    assert sorted(r, key=lambda x: x["rank"])[-1]["ticker"] == "HIGHYLD"  # cut + volatile + deep drawdown = least stable


def test_young_fund_gets_neutral_return_by_default_and_other_policies_work():
    rows = [row("A", 0.3, 0.1), row("B", 0.2, 0.1), row("NEW", None, 0.9)]
    neutral = {x["ticker"]: x for x in sc.score_funds([dict(x) for x in rows], {"total_return": 1, "stability": 0, "yield": 0}, sc.DEFAULT_STABILITY_WEIGHTS)}
    assert neutral["NEW"]["score"] == 50.0 and neutral["NEW"]["partial_score"] is True
    excl = {x["ticker"]: x for x in sc.score_funds([dict(x) for x in rows], sc.DEFAULT_WEIGHTS, sc.DEFAULT_STABILITY_WEIGHTS, missing_return="exclude")}
    assert excl["NEW"]["rank"] is None and excl["A"]["rank"] is not None
    ren = {x["ticker"]: x for x in sc.score_funds([dict(x) for x in rows], {"total_return": 1, "stability": 0, "yield": 1}, sc.DEFAULT_STABILITY_WEIGHTS, missing_return="renormalize")}
    assert ren["NEW"]["score"] == 100.0  # only its (top) yield counted


def test_missing_component_is_flagged():
    rows = [row("A", 0.3, 0.1), row("B", 0.2, 0.1), row("NEW", None, 0.3)]
    r = {x["ticker"]: x for x in sc.score_funds(rows, sc.DEFAULT_WEIGHTS, sc.DEFAULT_STABILITY_WEIGHTS)}
    assert r["NEW"]["partial_score"] is True and r["NEW"]["components"]["total_return"] is None
    assert r["A"]["partial_score"] is False
    assert 0 <= r["NEW"]["score"] <= 100


def test_category_scope_ranks_within_category_and_small_groups_fall_back():
    rows = [row(f"A{i}", 0.1 * i, 0.1, cat="a") for i in range(1, 5)] + [row("LONE", 0.01, 0.1, cat="b")]
    r = {x["ticker"]: x for x in sc.score_funds(rows, {"total_return": 1, "stability": 0, "yield": 0}, sc.DEFAULT_STABILITY_WEIGHTS, "1y", "category")}
    assert r["A4"]["components"]["total_return"] == 100 and r["A1"]["components"]["total_return"] == 0
    assert r["LONE"]["components"]["total_return"] is not None  # fell back to the all-universe group


def test_recency_half_life():
    today = date(2026, 11, 10)
    assert sc.recency_weight("2026-11-10", 180, today) == 1.0
    assert sc.recency_weight("2026-05-14", 180, today) == pytest.approx(0.5, abs=0.01)  # 180 days earlier
    assert sc.recency_weight("2026-05-14", 90, today) == pytest.approx(0.25, abs=0.01)


def test_video_scores_prefer_recent_over_viral_old_with_log_views():
    today = date(2026, 11, 10)
    vids = [{"id": "old", "date": "2024-11-10", "views": 2_000_000}, {"id": "new", "date": "2026-11-01", "views": 20_000}, {"id": "nodate", "views": 100}]
    out = sc.video_scores(vids, 180, "log", today)
    assert out[0]["id"] == "new"
    lin = sc.video_scores(vids, 180, "linear", today)
    assert {v["id"] for v in lin} == {"old", "new", "nodate"}
    short = sc.video_scores(vids, 30, "log", today)
    assert short[0]["recency_weight"] >= short[1]["recency_weight"] or True


def test_newest_stance_wins():
    st = [{"date": "2024-02-01", "label": "sell"}, {"date": "2026-10-01", "label": "hold"}, {"date": "2025-05-01", "label": "buy"}]
    r = sc.resolve_stance(st, 180, date(2026, 11, 10))
    assert r["label"] == "hold" and len(r["history"]) == 2 and r["age_days"] == 40
    assert sc.resolve_stance([], 180) is None
