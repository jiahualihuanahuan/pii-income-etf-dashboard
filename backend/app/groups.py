"""Broad groups for the detailed categories in config/tickers.yaml.

Every fund keeps its detailed `category` (secondary field) and gets a broad `group` plus, for sector funds,
a `subsector`. Explicit `group` / `subsector` on a fund in tickers.yaml always win; otherwise they are derived
here from the category (and, where a category mixes things, from the fund's name). `tools/assign_groups.py`
writes the derived values into tickers.yaml so they can be reviewed and edited by hand.
"""
from __future__ import annotations

import re
from typing import Optional

GROUPS = {
    "diversified": {"label": "Diversified", "description": "Broad-market, regional and multi-sector / all-in-one income funds (S&P 500, Nasdaq-100, TSX, Russell 2000, global, baskets)"},
    "sector": {"label": "Sector-specific", "description": "Funds concentrated in one sector: tech, healthcare, utilities, financials, energy, real estate, infrastructure"},
    "single_stock": {"label": "Single stock", "description": "Single-stock / option-income funds (YieldMax-style, funds of them)"},
    "crypto": {"label": "Bitcoin / crypto", "description": "Bitcoin and crypto-related income funds, including bitcoin-proxy stocks (MSTR)"},
    "commodities": {"label": "Commodities", "description": "Gold, silver, mining / materials and other commodity-linked income funds"},
    "other": {"label": "Other", "description": "Split-share corporations and anything that does not fit the groups above"},
}

# old category -> broad group
CATEGORY_GROUP = {
    "bitcoin": "crypto",
    "blended": "diversified",
    "canada": "diversified",
    "energy": "sector",
    "financials": "sector",
    "global": "diversified",
    "gold": "commodities",
    "nasdaq": "diversified",   # tech-only funds in this category are moved to sector/Tech by name (see below)
    "other": "diversified",
    "sector": "sector",
    "single_stock": "single_stock",
    "smallcap": "diversified",
    "sp500": "diversified",
    "split": "other",
}

# old category -> subsector when the whole category is one sector
CATEGORY_SUBSECTOR = {"energy": "Energy", "financials": "Financials"}

# name rules, first match wins: (group, subsector, regex on fund name)
NAME_RULES = {
    "nasdaq": [("sector", "Tech", r"\btech(nology)?\b|(?-i:\bAI\b)|FANG|\bTitans\b|Information Technology")],
    "sector": [("sector", "Utilities", r"utilit"), ("sector", "Healthcare", r"health\s?care"),
               ("sector", "Real estate", r"\bREITs?\b|real estate"), ("sector", "Infrastructure", r"infrastructure")],
    "gold": [("commodities", "Silver", r"silver"), ("commodities", "Gold", r"\bgold\b"),
             ("commodities", "Mining & materials", r"materials|mining|commodity|commodities")],
    "bitcoin": [("crypto", "Bitcoin-proxy stock (MSTR)", r"Strategy Inc"), ("crypto", "Crypto equities", r"Crypto Industry"),
                ("crypto", "Bitcoin", r"bitcoin")],
    "split": [("other", "Real estate", r"real estate"), ("other", "Preferred shares", r"preferred"),
              ("other", "Financials", r"financial|banc|lifeco|life & banc"), ("other", "Dividend / equity split", r".")],
}


def classify(fund: dict, category_groups: Optional[dict] = None) -> tuple[str, Optional[str]]:
    """-> (group, subsector). Explicit values on the fund win."""
    cat = fund.get("category")
    group, sub = fund.get("group"), fund.get("subsector")
    if group and (sub or group not in ("sector",)):
        return group, sub
    d_group, d_sub = {**CATEGORY_GROUP, **(category_groups or {})}.get(cat, "other"), CATEGORY_SUBSECTOR.get(cat)
    for g, s, rx in NAME_RULES.get(cat, []):
        if re.search(rx, fund.get("name") or "", re.I):
            d_group, d_sub = g, s
            break
    if cat == "sector" and d_group == "sector" and not d_sub:
        d_sub = "Other sector"
    return group or d_group, sub or d_sub


def apply(universe: dict) -> dict:
    """Fill `group` / `subsector` on every fund and add `groups` (definitions) to the universe dict."""
    defs = {k: dict(v) for k, v in GROUPS.items()}
    for k, v in (universe.get("groups") or {}).items():
        defs.setdefault(k, {}).update(v)
    universe["groups"] = defs
    for f in universe.get("funds", []):
        f["group"], sub = classify(f, universe.get("category_groups"))
        if sub or "subsector" in f:
            f["subsector"] = sub
    return universe
