#!/usr/bin/env python3
"""Check which symbols exist on Yahoo Finance (via yfinance) and cache the result in a JSON file.

    python tools/verify_symbols.py --cache ../raw/verify_cache.json HHIS.TO HYLD-U.TO ...
    python tools/verify_symbols.py --cache ../raw/verify_cache.json --file symbols.txt

Result per symbol: status ok | nodata | error | ratelimited, rows, name, currency, instrument type, timestamp.
Only a clean 'nodata' (no 429) counts as "not on Yahoo". Be gentle: ~3 s between calls, 2 min back-off on rate limits.
"""
import argparse
import json
import logging
import os
import sys
import time

import yfinance as yf

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def verify(symbols, cache_path, pause=2.5):
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    for s in dict.fromkeys(symbols):
        if s in cache and cache[s].get("status") in ("ok", "nodata"):
            continue
        for _ in range(4):
            try:
                t = yf.Ticker(s)
                h = t.history(period="1mo")
                md = t.history_metadata or {}
                cache[s] = {"status": "ok" if len(h) else "nodata", "rows": len(h), "name": md.get("longName") or md.get("shortName"),
                            "cur": md.get("currency"), "type": md.get("instrumentType"), "ts": time.strftime("%F %T")}
                break
            except Exception as e:
                if "Too Many" in str(e) or "Rate" in str(e):
                    cache[s] = {"status": "ratelimited"}
                    time.sleep(120)
                else:
                    cache[s] = {"status": "error", "err": str(e)[:100]}
                    break
        json.dump(cache, open(cache_path, "w"), indent=0)
        time.sleep(pause)
    return cache


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--file")
    ap.add_argument("symbols", nargs="*")
    a = ap.parse_args()
    syms = list(a.symbols) + (open(a.file).read().split() if a.file else [])
    c = verify(syms, a.cache)
    print({s: c[s]["status"] for s in syms})
