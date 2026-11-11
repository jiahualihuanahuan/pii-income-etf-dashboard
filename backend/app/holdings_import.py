"""Import ETF holdings into the dashboard's SQLite database.

    # on the machine that has the portfolio-xray index: build a bundle (no DB touched)
    python -m app.holdings_import --from-xray /path/to/portfolio-xray/data --write-bundle pii_holdings_bundle.json.gz
    # anywhere: load a bundle into the dashboard DB (replaces the holdings tables)
    python -m app.holdings_import --bundle /data/pii_holdings_bundle.json.gz
    # both in one go
    python -m app.holdings_import --from-xray /path/to/portfolio-xray/data

The bundle is a data file: keep it out of git (``*.holdings*.json.gz`` / ``*bundle*.json.gz`` are in .gitignore).
Only the local SQLite file (DATA_DIR/app.db) is ever written.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db, holdings
from .config import paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, help="holdings bundle (.json.gz) to load")
    ap.add_argument("--from-xray", type=Path, help="portfolio-xray data dir (funds.csv, universe.csv, holdings_*.csv.gz)")
    ap.add_argument("--tickers", type=Path, help="tickers.yaml to read the dashboard universe from (default: config/tickers.yaml)")
    ap.add_argument("--write-bundle", type=Path, help="write the bundle built from --from-xray here")
    ap.add_argument("--no-db", action="store_true", help="only write the bundle; do not touch the database")
    a = ap.parse_args(argv)
    if not (a.bundle or a.from_xray):
        ap.error("give --bundle or --from-xray")
    if a.bundle:
        bundle = holdings.load_bundle(a.bundle)
    else:
        from . import holdings_build
        bundle = holdings_build.build_bundle(a.from_xray, a.tickers or paths()["config_dir"] / "tickers.yaml")
        if a.write_bundle:
            holdings_build.write_bundle(bundle, a.write_bundle)
            print(f"bundle written: {a.write_bundle} ({a.write_bundle.stat().st_size / 1e6:.2f} MB)")
    if a.no_db:
        return 0
    db.init()
    n = holdings.import_bundle(bundle)
    print(f"imported {n['funds']} funds, {n['holdings']} holdings rows, {n['aliases']} dashboard symbols -> {db.db_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
