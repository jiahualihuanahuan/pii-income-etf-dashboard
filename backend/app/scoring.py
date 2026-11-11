"""Weighted ranking score and recency weighting. Pure functions."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime
from typing import Iterable, Optional

DEFAULT_WEIGHTS = {"total_return": 0.45, "stability": 0.30, "yield": 0.25}
DEFAULT_STABILITY_WEIGHTS = {"cuts": 0.35, "cv": 0.15, "volatility": 0.25, "drawdown": 0.25}
TR_WINDOWS = ["1m", "3m", "6m", "1y", "3y", "inception"]
MIN_CATEGORY_SIZE = 3


def normalize(weights: dict, defaults: dict) -> dict:
    """Clamp negatives to 0 and scale to sum 1. Falls back to defaults if everything is 0."""
    w = {k: max(float(weights.get(k, 0) or 0), 0.0) for k in defaults}
    s = sum(w.values())
    if s <= 0:
        w, s = dict(defaults), sum(defaults.values())
    return {k: v / s for k, v in w.items()}


def pct_rank(values: list[Optional[float]], higher_is_better: bool = True) -> list[Optional[float]]:
    """Percentile rank 0..100 (ties share the average rank). None stays None; n==1 -> 50."""
    idx = [(v, i) for i, v in enumerate(values) if v is not None and not (isinstance(v, float) and math.isnan(v))]
    out: list[Optional[float]] = [None] * len(values)
    n = len(idx)
    if n == 0:
        return out
    if n == 1:
        out[idx[0][1]] = 50.0
        return out
    idx.sort(key=lambda t: t[0], reverse=not higher_is_better)  # worst first
    i = 0
    while i < n:
        j = i
        while j + 1 < n and idx[j + 1][0] == idx[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            out[idx[k][1]] = 100.0 * avg_rank / (n - 1)
        i = j + 1
    return out


def tr_value(m: dict, window: str) -> Optional[float]:
    return m.get("tr_inception") if window == "inception" else (m.get("tr") or {}).get(window)


def _weighted_mean(parts: list[tuple[Optional[float], float]]) -> Optional[float]:
    av = [(v, w) for v, w in parts if v is not None and w > 0]
    if not av:
        return None
    return sum(v * w for v, w in av) / sum(w for _, w in av)


def score_funds(rows: list[dict], weights: dict, stability_weights: dict, window: str = "1y", scope: str = "all",
                missing_return: str = "neutral") -> list[dict]:
    """Attach `score`, `components` and `rank` to each row (dict with a 'metrics' dict and 'category').

    components are percentile ranks (0-100, higher is better) of: total return over `window`,
    stability (weighted mean of 4 sub-percentiles) and TTM yield. Missing components are dropped and the
    remaining weights re-normalised (`partial_score` = True). With scope='category' percentiles are computed
    inside each broad group (falls back to `category` when a row has no group; groups with fewer than 3 scorable funds fall back to the whole universe).

    `missing_return` = what to do when a fund is too young for the chosen window: "neutral" (default) gives the
    return component a neutral 50 so a young high-yielder is not rewarded for having no track record, "renormalize"
    drops the component and re-weights the rest, "exclude" leaves the fund unranked. Always flagged `partial_score`.
    """
    w = normalize(weights, DEFAULT_WEIGHTS)
    sw = normalize(stability_weights, DEFAULT_STABILITY_WEIGHTS)
    groups: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        key = (r.get("group") or r.get("category")) if scope == "category" else "__all__"
        groups[key].append(i)
    if scope == "category":
        for k in [k for k, v in groups.items() if len(v) < MIN_CATEGORY_SIZE]:
            groups.pop(k)
        covered = {i for v in groups.values() for i in v}
        groups["__all__"] = [i for i in range(len(rows)) if i not in covered]
    for idxs in groups.values():
        ms = [rows[i]["metrics"] for i in idxs]
        tr = pct_rank([tr_value(m, window) for m in ms], True)
        yl = pct_rank([m.get("ttm_yield") for m in ms], True)
        cuts = pct_rank([m.get("cut_severity_12m") if m.get("n_payments_12m") else None for m in ms], False)
        cv = pct_rank([m.get("cv_12m") for m in ms], False)
        vol = pct_rank([m.get("vol_1y") for m in ms], False)
        dd = pct_rank([-(m.get("maxdd_1y")) if m.get("maxdd_1y") is not None else None for m in ms], False)
        for k, i in enumerate(idxs):
            stab = _weighted_mean([(cuts[k], sw["cuts"]), (cv[k], sw["cv"]), (vol[k], sw["volatility"]), (dd[k], sw["drawdown"])])
            comp = {"total_return": tr[k], "stability": stab, "yield": yl[k]}
            no_ret = comp["total_return"] is None
            used = dict(comp)
            if no_ret and missing_return == "neutral":
                used["total_return"] = 50.0
            score = _weighted_mean([(used[n], w[n]) for n in used])
            if no_ret and missing_return == "exclude":
                score = None
            rows[i]["components"] = {n: (None if v is None else round(v, 1)) for n, v in comp.items()}
            rows[i]["score"] = None if score is None else round(score, 1)
            rows[i]["partial_score"] = (any(v is None for v in comp.values()) and score is not None)
    ranked = sorted([r for r in rows if r.get("score") is not None], key=lambda r: (-r["score"], r.get("ticker", "")))
    for n, r in enumerate(ranked, 1):
        r["rank"] = n
    for r in rows:
        r.setdefault("rank", None)
    return rows


# ---------- recency weighting ----------

def _to_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def age_days(d, today: Optional[date] = None) -> int:
    return max((_to_date(today or date.today()) - _to_date(d)).days, 0)


def recency_weight(d, half_life_days: float = 180.0, today: Optional[date] = None) -> float:
    """0.5 ** (age / half_life); 1.0 for today, 0.5 after one half-life."""
    hl = max(float(half_life_days), 1.0)
    return 0.5 ** (age_days(d, today) / hl)


def views_transform(views: Optional[int], mode: str = "log") -> float:
    v = max(views or 0, 0)
    if mode == "linear":
        return float(v)
    if mode == "none":
        return 1.0
    return math.log10(1 + v)


def video_scores(videos: Iterable[dict], half_life_days: float = 180.0, mode: str = "log", today: Optional[date] = None) -> list[dict]:
    """score = transform(views) * recency_weight. Videos without a date use `est_date` if present, else are
    treated as ~2 half-lives old. Returns new dicts sorted by score desc."""
    out = []
    for v in videos:
        d = v.get("date") or v.get("est_date")
        w = recency_weight(d, half_life_days, today) if d else 0.25
        s = views_transform(v.get("views"), mode) * w
        out.append({**v, "recency_weight": round(w, 4), "score": round(s, 4)})
    out.sort(key=lambda x: (-x["score"], x.get("date") or ""))
    return out


def resolve_stance(stances: list[dict], half_life_days: float = 180.0, today: Optional[date] = None) -> Optional[dict]:
    """Newest dated stance wins. Returns it with age_days/recency_weight, plus `history` of older ones."""
    if not stances:
        return None
    ordered = sorted(stances, key=lambda s: str(s["date"]), reverse=True)
    cur = dict(ordered[0])
    cur["age_days"] = age_days(cur["date"], today)
    cur["recency_weight"] = round(recency_weight(cur["date"], half_life_days, today), 4)
    cur["history"] = [{**s, "age_days": age_days(s["date"], today),
                       "recency_weight": round(recency_weight(s["date"], half_life_days, today), 4)} for s in ordered[1:]]
    return cur
