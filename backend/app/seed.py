"""Load shipped seed JSON (videos, mentions) into SQLite. Idempotent (INSERT OR IGNORE; views refreshed)."""
from __future__ import annotations

import json

from . import db
from .config import load_seed


def load() -> dict:
    videos = load_seed("videos.json") or []
    mentions = load_seed("mentions.json") or {}
    nv = nm = 0
    with db.session() as con:
        for v in videos:
            cur = con.execute(
                "INSERT OR IGNORE INTO videos(id,title,date,est_date,views,duration,has_transcript,tickers,source) VALUES (?,?,?,?,?,?,?,?,?)",
                (v["id"], v["title"], v.get("date"), v.get("est_date"), v.get("views"), v.get("duration"),
                 1 if v.get("has_transcript") else 0, json.dumps(v.get("tickers") or {}), "seed"))
            nv += cur.rowcount
        have = con.execute("SELECT COUNT(*) c FROM mentions WHERE 1").fetchone()["c"]
        if have == 0:
            for tk, items in mentions.items():
                for it in items:
                    con.execute("INSERT INTO mentions(ticker,video_id,date,t,snippet,n) VALUES (?,?,?,?,?,?)",
                                (tk, it["video_id"], it.get("date"), it.get("t"), it["snippet"], it.get("n", 1)))
                    nm += 1
    db.bump()
    return {"videos_added": nv, "mentions_added": nm}
