#!/usr/bin/env python3
"""Write the broad `group` / `subsector` of every fund (derived by backend/app/groups.py) into config/tickers.yaml.

Idempotent; explicit values already in the file are kept. Review the result with `git diff config/tickers.yaml`.
    python tools/assign_groups.py [--check]
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import groups  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if tickers.yaml is not up to date")
    a = ap.parse_args()
    path = ROOT / "config" / "tickers.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["groups"] = {k: dict(v) for k, v in groups.GROUPS.items()}
    doc["category_groups"] = dict(groups.CATEGORY_GROUP)
    for f in doc["funds"]:
        g, s = groups.classify(f)
        f["group"] = g
        if s:
            f["subsector"] = s
    # keep the usual key order: ..., categories, groups, category_groups, funds, excluded, unresolved
    order = [k for k in ("version", "generated", "notes", "categories", "groups", "category_groups", "funds", "excluded", "unresolved") if k in doc]
    doc = {k: doc[k] for k in order} | {k: v for k, v in doc.items() if k not in order}
    out = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140, default_flow_style=False)
    if a.check:
        sys.exit(0 if out == path.read_text(encoding="utf-8") else 1)
    path.write_text(out, encoding="utf-8")
    print("updated", path)


if __name__ == "__main__":
    main()
