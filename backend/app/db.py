"""SQLite storage (stdlib sqlite3)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .config import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (ticker TEXT NOT NULL, d TEXT NOT NULL, close REAL NOT NULL, PRIMARY KEY (ticker, d));
CREATE TABLE IF NOT EXISTS dividends (ticker TEXT NOT NULL, d TEXT NOT NULL, amount REAL NOT NULL, PRIMARY KEY (ticker, d));
CREATE TABLE IF NOT EXISTS fund_meta (ticker TEXT PRIMARY KEY, last_price_fetch TEXT, last_div_fetch TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY, title TEXT, date TEXT, est_date TEXT, views INTEGER, duration INTEGER,
  has_transcript INTEGER DEFAULT 0, tickers TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS mentions (
  ticker TEXT NOT NULL, video_id TEXT NOT NULL, date TEXT, t INTEGER, snippet TEXT, n INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS mentions_ticker ON mentions (ticker, date);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, started TEXT, finished TEXT, status TEXT, message TEXT);
"""

_lock = threading.RLock()
_version = 0  # bumped on every data write; used to invalidate the in-memory metrics cache


def version() -> int:
    return _version


def bump() -> None:
    global _version
    _version += 1


def db_path() -> Path:
    d = paths()["data_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d / "app.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path(), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextmanager
def session():
    with _lock:
        con = connect()
        try:
            yield con
            con.commit()
        finally:
            con.close()


def init() -> None:
    with session() as con:
        con.executescript(SCHEMA)


def upsert_prices(con, ticker: str, close: pd.Series) -> int:
    rows = [(ticker, ix.strftime("%Y-%m-%d"), float(v)) for ix, v in close.dropna().items()]
    con.executemany("INSERT OR REPLACE INTO prices(ticker,d,close) VALUES (?,?,?)", rows)
    bump()
    return len(rows)


def upsert_dividends(con, ticker: str, divs: pd.Series) -> int:
    rows = [(ticker, ix.strftime("%Y-%m-%d"), float(v)) for ix, v in divs.dropna().items() if v > 0]
    con.executemany("INSERT OR REPLACE INTO dividends(ticker,d,amount) VALUES (?,?,?)", rows)
    bump()
    return len(rows)


def load_series(con, ticker: str) -> tuple[pd.Series, pd.Series]:
    p = con.execute("SELECT d, close FROM prices WHERE ticker=? ORDER BY d", (ticker,)).fetchall()
    d = con.execute("SELECT d, amount FROM dividends WHERE ticker=? ORDER BY d", (ticker,)).fetchall()
    close = pd.Series([r["close"] for r in p], index=pd.to_datetime([r["d"] for r in p]), dtype=float)
    divs = pd.Series([r["amount"] for r in d], index=pd.to_datetime([r["d"] for r in d]), dtype=float)
    return close, divs


def last_price_date(con, ticker: str) -> Optional[str]:
    r = con.execute("SELECT MAX(d) m FROM prices WHERE ticker=?", (ticker,)).fetchone()
    return r["m"] if r else None


def count_prices(con) -> int:
    return con.execute("SELECT COUNT(DISTINCT ticker) c FROM prices").fetchone()["c"]


def start_job(name: str) -> int:
    with session() as con:
        cur = con.execute("INSERT INTO job_runs(name,started,status) VALUES (?,?,?)", (name, datetime.now().isoformat(timespec="seconds"), "running"))
        return cur.lastrowid


def finish_job(job_id: int, status: str, message: str = "") -> None:
    with session() as con:
        con.execute("UPDATE job_runs SET finished=?, status=?, message=? WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), status, message[:2000], job_id))


def recent_jobs(limit: int = 20) -> list[dict]:
    with session() as con:
        return [dict(r) for r in con.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))]
