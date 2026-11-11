#!/usr/bin/env python3
"""Build config/tickers.yaml and seed/{videos,mentions}.json from the raw research folder.

The raw research (transcripts, yt-dlp metadata, caption-ticker votes, Yahoo verification cache) lives OUTSIDE this
repo (large, third-party). Point --raw at it. The tool is idempotent: re-run it after fetching more transcripts.

    python tools/build_seed.py --raw ../passiveincomeinvesting
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import groups  # noqa: E402
from app.mentions import find_mentions, fmt_t, parse, secs_at, sentences  # noqa: E402

STOP = set("ETF US NASDAQ TSX TFSA AUM NAV CEO RRSP EAFE CIBC MSFT JP DIY SPX VIX FY EIT GIF CBN CLO CIO TC SL OG MSI IQQ ENS NK QQ HB AF PIK LRA SNP MAG IH HEE ZW".split())

CATEGORIES = {
    "blended": ("All-in-one baskets (Canada)", "Funds of funds / income-share baskets: HHIS, BMAX, EQCL, HDIF, USHI, HHIC..."),
    "sp500": ("S&P 500 income", "Covered-call / put-write / premium-income funds on the S&P 500"),
    "nasdaq": ("Nasdaq-100 & tech income", "Covered-call and boosted-income funds on the Nasdaq-100 or technology"),
    "canada": ("Canadian equity income", "TSX / Canadian dividend-stock covered-call funds"),
    "financials": ("Financials & banks income", "Bank and financial-sector income funds"),
    "energy": ("Energy income", "Oil & gas / energy covered-call funds"),
    "gold": ("Gold, resources & commodities income", "Gold-producer, materials and commodity income funds"),
    "sector": ("Other sector income", "Utilities, healthcare, REITs, infrastructure and other sector funds"),
    "bitcoin": ("Bitcoin income", "Bitcoin / Bitcoin-proxy income funds"),
    "split": ("Split-share & closed-end", "Split corporations and preferred-share funds"),
    "smallcap": ("Russell 2000 income", "Small-cap (Russell 2000) covered-call / high-income funds"),
    "global": ("Global / international income", "Global and non-US/Canada income funds"),
    "single_stock": ("Single-stock / option-income", "YieldMax / Defiance / Roundhill-style option income funds"),
    "other": ("Other income funds", "Everything else he ranks"),
}
CAT_RULES = [  # first match wins, tested against the fund name
    ("bitcoin", r"bitcoin|\bBTC\b|MicroStrategy|Strategy Inc|crypto|Strategy .*Income"),
    ("split", r"Split|Banc Corp|Financial 15|Preferred ?Share"),
    ("gold", r"\bGold\b|Silver|Commodit|Mining|Materials|Uranium"),
    ("energy", r"\bOil\b|Energy|\bGas\b"),
    ("financials", r"Financials|Bank|Lifeco"),
    ("blended", r"All-in-One|All-Equity|Multi-Asset|Asset Allocation|High Income Shares|HighShares|HighPay|Diversified|High Income Equity|Balanced"),
    ("sector", r"Utilit|Health|REIT|Infrastructure|Real Estate|Industrial|Consumer"),
    ("smallcap", r"Russell 2000"),
    ("single_stock", r"YieldMax|Option Income|Daily Target"),
    ("nasdaq", r"NASDAQ|Nasdaq|Technology|Tech |Information Tech|\bAI\b|FANG"),
    ("sp500", r"S&P 500|S&P500|SPX|U\.S\.|\bUS\b|BuyWrite|Large Cap|Premium Income|Equity Premium|Overlay"),
    ("canada", r"Canadian|Canada|TSX|\bCA\b"),
    ("global", r"Global (?!X)|International|World|Europe|EAFE"),
]


def load_json(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def listing_of(sym: str) -> str:
    return "USD" if (sym.endswith("-U.TO") or not sym.endswith(".TO")) else "CAD"


def categorize(name: str) -> str:
    for cat, rx in CAT_RULES:
        if re.search(rx, name or "", re.I if cat in ("bitcoin", "split") else 0):
            return cat
    return "other"


def date_of(v: dict) -> str | None:
    d = v.get("date") or v.get("edate")
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if d else None


class Raw:
    def __init__(self, raw: Path):
        self.raw = raw
        self.votes = load_json(raw / "votes.json", {})
        self.names = load_json(raw / "names_all.json", {})
        self.verify = load_json(raw / "verify_cache.json", {})
        self.index = {v["id"]: v for v in load_json(raw / "index2.json", [])}

    def info(self, sym: str):
        """-> (name, currency, ok) from the Yahoo verification caches"""
        v = self.verify.get(sym)
        if v and v.get("status") == "ok":
            return v.get("name"), v.get("cur"), True
        n = self.names.get(sym)
        if n and n.get("type") in ("ETF", "MUTUALFUND", "EQUITY"):
            return n.get("name"), n.get("cur"), True
        return None, None, False

    def type_of(self, sym: str):
        n = self.names.get(sym)
        if n:
            return n.get("type")
        v = self.verify.get(sym)
        return (v or {}).get("type")


def select(raw: Raw, spec: dict):
    alias = spec["alias"]
    votes = defaultdict(lambda: {"CA": 0, "US": 0})
    spoken = defaultdict(set)
    for t, x in raw.votes.items():
        c = alias.get(t, t)
        votes[c]["CA"] += x["CA"]
        votes[c]["US"] += x["US"]
        spoken[c].add(t)
    chosen: dict[str, dict] = {}
    excluded, unresolved = [], []
    allow_eq = re.compile(spec["allow_equity_name_regex"], re.I)
    for tok, v in sorted(votes.items(), key=lambda kv: -(kv[1]["CA"] + kv[1]["US"])):
        tot = v["CA"] + v["US"]
        if tok in STOP:
            continue
        if tok in spec["exclude"]:
            if tot >= 5:
                excluded.append({"token": tok, "mentions": tot, "reason": spec["exclude"][tok]})
            continue
        if tot < 2 and tok not in spec["both"] and tok not in spec["force"]:
            continue
        if tok in spec["both"]:
            syms = spec["both"][tok]
        elif tok in spec["force"]:
            syms = [spec["force"][tok]]
        elif tok.endswith(".U"):
            syms = [tok[:-2] + "-U.TO"]
        else:
            order = [tok + ".TO", tok] if v["CA"] >= v["US"] else [tok, tok + ".TO"]
            syms = []
            for s in order:
                name, cur, ok = raw.info(s)
                if not ok:
                    continue
                typ = raw.type_of(s)
                if typ in ("ETF", "MUTUALFUND") or (typ == "EQUITY" and allow_eq.search(name or "")) or s in spec["benchmarks"]:
                    syms = [s]
                    break
        found = False
        for s in syms:
            name, cur, ok = raw.info(s)
            if not ok:
                continue
            found = True
            chosen[s] = {"token": tok, "name": name, "mentions": tot, "ca": v["CA"], "us": v["US"], "spoken": sorted(spoken[tok])}
        if not found and tok in spec["unresolved_notes"]:
            unresolved.append({"token": tok, "mentions": tot, "ca_votes": v["CA"], "us_votes": v["US"],
                               "note": spec["unresolved_notes"][tok]})
    for tok, note in spec["unresolved_notes"].items():
        if tok not in [u["token"] for u in unresolved] and not any(c["token"] == tok for c in chosen.values()):
            v = votes.get(tok, {"CA": 0, "US": 0})
            unresolved.append({"token": tok, "mentions": v["CA"] + v["US"], "ca_votes": v["CA"], "us_votes": v["US"], "note": note})
    for s in spec.get("extra", []):
        name, cur, ok = raw.info(s)
        if ok and s not in chosen:
            chosen[s] = {"token": s, "name": name, "mentions": 0, "ca": 0, "us": 0, "spoken": ["T-SPY"] if s == "TSPY" else []}
    # benchmarks always included (they are fetched but not ranked)
    for s in spec["benchmarks"]:
        name, cur, ok = raw.info(s)
        if ok and s not in chosen:
            chosen[s] = {"token": s.split(".")[0], "name": name, "mentions": 0, "ca": 0, "us": 0, "spoken": []}
    return chosen, excluded, unresolved


def add_usd_pairs(chosen: dict, raw: Raw, spec: dict):
    """For each ranked CAD fund, add the -U.TO listing if Yahoo has it (cache from verify_symbols.py)."""
    for s in list(chosen):
        if s.endswith(".TO") and not s.endswith("-U.TO") and s not in spec["benchmarks"]:
            u = s[:-3] + "-U.TO"
            name, cur, ok = raw.info(u)
            if ok and u not in chosen:
                base = chosen[s]
                chosen[u] = {**base, "name": name or base["name"], "mentions": 0, "ca": 0, "us": 0, "pair_of": s}


def build_funds(chosen, spec, stances_by_ticker, latest_vid_for, raw: Raw):
    bm = spec["benchmarks"]
    funds = []
    for sym, c in chosen.items():
        name = c["name"] or sym
        listing = listing_of(sym)
        role = "benchmark" if sym in bm else "fund"
        cat = categorize(name)
        cat = spec.get("category_override", {}).get(sym, cat)
        b = spec["benchmark_override"].get(sym) or spec["benchmark_map"][listing].get(cat if role == "fund" else "-")
        if sym in ("HHIS-U.TO",):
            b = spec["benchmark_override"].get(sym)
        if role == "fund" and b is None and listing == "CAD" and cat in ("single_stock", "other", "sector"):
            b = spec["benchmark_map"]["CAD"].get("sp500") if cat != "sector" else None
        if b and b not in chosen:
            b = None
        f = {"ticker": sym, "name": name, "listing": listing, "category": cat, "role": role}
        if role == "fund" and b:
            f["benchmark"] = b
        spoken = sorted({sym.split(".")[0].split("-")[0], *c["spoken"]} - {""})
        f["aliases"] = spoken
        if c.get("pair_of"):
            f["pair"] = c["pair_of"]
        f["mentions_in_ranking_videos"] = c["mentions"]
        st = stances_by_ticker.get(sym) or stances_by_ticker.get(sym.replace("-U.TO", ".TO")) or []
        if st:
            f["stances"] = st
        funds.append(f)
    # symmetric pair links
    by = {f["ticker"]: f for f in funds}
    for t, f in by.items():
        if t.endswith("-U.TO") and t[:-5] + ".TO" in by and f["role"] == "fund":
            f["pair"] = t[:-5] + ".TO"
    for f in funds:
        p = f.get("pair")
        if p and p in by:
            by[p]["pair"] = f["ticker"]
    funds.sort(key=lambda f: (f["role"] != "fund", -f["mentions_in_ranking_videos"], f["ticker"]))
    return funds


def clip(sentence: str, m: re.Match | None, n: int = 300) -> str:
    if len(sentence) <= n:
        return sentence
    a = max(m.start() - 110, 0) if m else 0
    cut = sentence[a:a + n]
    if a > 0:
        cut = "…" + cut[cut.find(" ") + 1:]
    return cut.rsplit(" ", 1)[0] + "…"


def build_stances(raw: Raw, specs: dict):
    out: dict[str, list] = defaultdict(list)
    errors = []
    vtitle = specs["videos"]
    cache: dict[str, tuple] = {}
    for s in specs["stances"]:
        vid = s["video"]
        v = raw.index.get(vid)
        if not v:
            errors.append(f"video {vid} not in index")
            continue
        entry = {"date": date_of(v), "label": s["label"], "video_id": vid, "video_title": vtitle.get(vid) or v["title"],
                 "url": f"https://youtu.be/{vid}"}
        if s.get("summary"):
            entry["summary"] = s["summary"]
        if s.get("pattern"):
            tp = raw.raw / "tx" / f"{vid}.txt"
            if not tp.exists():
                errors.append(f"no transcript for {vid}")
                continue
            if vid not in cache:
                plain, marks = parse(tp.read_text())
                cache[vid] = (plain, marks, sentences(plain))
            plain, marks, sents = cache[vid]
            hit = None
            for off, sent in sents:
                m = re.search(s["pattern"], sent, re.I)
                if m:
                    hit = (off, sent, m)
                    break
            if not hit:
                errors.append(f"pattern not found: {s['ticker']} {vid} /{s['pattern']}/")
                continue
            off, sent, m = hit
            t = secs_at(marks, off)
            entry["quote"] = clip(sent, m)
            if t is not None:
                entry["t"] = fmt_t(t)
                entry["url"] = f"https://youtu.be/{vid}?t={t}"
        elif not s.get("summary"):
            errors.append(f"stance without pattern or summary: {s['ticker']}")
            continue
        out[s["ticker"]].append(entry)
    return out, errors


def scan_transcripts(raw: Raw, funds: list[dict]):
    """-> per-video fund counts and per-base-ticker snippet candidates (best snippet per video)."""
    names: dict[str, list[str]] = {}
    for f in funds:
        base = f["ticker"].split(".")[0].split("-")[0]
        names.setdefault(base, set()).update(f["aliases"])
    names = {k: sorted(v) for k, v in names.items()}
    per_video: dict[str, dict] = {}
    for tp in sorted((raw.raw / "tx").glob("*.txt")):
        vid = tp.stem
        text = tp.read_text()
        if len(text) < 200:
            continue
        per_video[vid] = find_mentions(text, names, max_snippets=2)
    return per_video


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, type=Path)
    ap.add_argument("--specs", type=Path, default=ROOT / "tools" / "specs")
    ap.add_argument("--out", type=Path, default=ROOT)
    a = ap.parse_args()
    raw = Raw(a.raw)
    spec = yaml.safe_load((a.specs / "universe_specs.yaml").read_text())
    sspec = yaml.safe_load((a.specs / "stance_specs.yaml").read_text())
    chosen, excluded, unresolved = select(raw, spec)
    add_usd_pairs(chosen, raw, spec)
    stances, errs = build_stances(raw, sspec)
    if errs:
        print("STANCE ERRORS:\n  " + "\n  ".join(errs), file=sys.stderr)
        sys.exit(2)
    funds = build_funds(chosen, spec, stances, None, raw)
    known = {f["ticker"] for f in funds}
    stray = [t for t in stances if t not in known]
    # stances for funds that did not resolve on Yahoo are kept visible on the 'unresolved' list instead
    for u in unresolved:
        ss = [s for t, v in stances.items() if t.split(".")[0] == u["token"] for s in v]
        if ss:
            u["his_latest_stance"] = sorted(ss, key=lambda s: s["date"], reverse=True)[0]
    used_cats = sorted({f["category"] for f in funds})
    for f in funds:  # broad group / subsector (backend/app/groups.py); the detailed category stays as the secondary field
        f["group"], sub = groups.classify(f)
        if sub:
            f["subsector"] = sub
    doc = {
        "version": 1,
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "notes": ("Editable. listing = trading currency of this symbol (CAD = .TO, USD = US tickers and -U.TO). role: fund (ranked) | benchmark "
                  "(fetched, shown, never ranked). A benchmark must have the same listing currency as the fund. stances: his dated opinions, "
                  "newest wins. Re-generate with tools/build_seed.py or edit by hand."),
        "categories": {k: {"label": CATEGORIES[k][0], "description": CATEGORIES[k][1]} for k in used_cats},
        "groups": {k: dict(v) for k, v in groups.GROUPS.items()},
        "category_groups": {k: groups.CATEGORY_GROUP[k] for k in used_cats},
        "funds": funds,
        "excluded": excluded,
        "unresolved": unresolved,
    }
    (a.out / "config").mkdir(exist_ok=True)
    (a.out / "config" / "tickers.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140, default_flow_style=False), encoding="utf-8")

    per_video = scan_transcripts(raw, funds)
    videos = []
    for vid, v in sorted(raw.index.items(), key=lambda kv: (date_of(kv[1]) or "", kv[0]), reverse=True):
        tk = {k: x["n"] for k, x in per_video.get(vid, {}).items()}
        rec = {"id": vid, "title": v["title"], "views": v.get("views"), "duration": v.get("dur")}
        if v.get("date"):
            rec["date"] = date_of(v)
        elif v.get("edate"):
            rec["est_date"] = date_of(v)
        if vid in per_video:
            rec["has_transcript"] = True
            rec["tickers"] = dict(sorted(tk.items(), key=lambda kv: -kv[1])[:12])
        videos.append(rec)
    (a.out / "seed").mkdir(exist_ok=True)
    (a.out / "seed" / "videos.json").write_text(json.dumps(videos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    mentions: dict[str, list] = defaultdict(list)
    for vid, found in per_video.items():
        d = date_of(raw.index.get(vid, {})) or None
        if not d:
            continue
        for tk, x in found.items():
            sn = x["snippets"][0]
            mentions[tk].append({"video_id": vid, "date": d, "t": sn["t"], "snippet": sn["snippet"], "n": x["n"]})
    keep = {tk: sorted(v, key=lambda m: m["date"], reverse=True)[:3] for tk, v in mentions.items()}
    (a.out / "seed" / "mentions.json").write_text(json.dumps(keep, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    nf = [f for f in funds if f["role"] == "fund"]
    print(f"funds ranked: {len(nf)} (CAD {sum(f['listing']=='CAD' for f in nf)}, USD {sum(f['listing']=='USD' for f in nf)}), "
          f"benchmarks: {len(funds)-len(nf)}, excluded: {len(excluded)}, unresolved: {len(unresolved)}, stances: {sum(len(v) for v in stances.values())}"
          f" on {len(stances)} tickers; stray stance tickers (not in universe): {stray}")
    print(f"videos: {len(videos)} (with transcript scanned: {len(per_video)}), mention keys: {len(keep)}")


if __name__ == "__main__":
    main()
