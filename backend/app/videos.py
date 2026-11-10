"""Weekly video sync: list channel (yt-dlp), read new videos' metadata, fetch transcripts
(youtube-transcript-api), tag fund mentions. Does not use any paid API. Network callables are injectable."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import db
from .config import paths
from .mentions import find_mentions, fmt_t

log = logging.getLogger("videos")


def ytdlp_list(channel_url: str) -> list[dict]:
    from yt_dlp import YoutubeDL
    with YoutubeDL({"quiet": True, "extract_flat": True, "skip_download": True}) as y:
        info = y.extract_info(channel_url.rstrip("/") + "/videos", download=False)
    return [{"id": e["id"], "title": e.get("title"), "views": e.get("view_count"), "duration": e.get("duration")}
            for e in (info.get("entries") or []) if e and e.get("id")]


def ytdlp_info(video_id: str) -> dict:
    from yt_dlp import YoutubeDL
    with YoutubeDL({"quiet": True, "skip_download": True}) as y:
        i = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    ud = i.get("upload_date")
    return {"date": f"{ud[:4]}-{ud[4:6]}-{ud[6:]}" if ud else None, "views": i.get("view_count"), "duration": i.get("duration")}


class Blocked(Exception):
    pass


def fetch_transcript(video_id: str) -> Optional[str]:
    """-> '[m:ss] text' lines, None if captions unavailable; raises Blocked if YouTube rate-limits the IP."""
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        tr = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US", "en-GB"])
    except Exception as e:
        name = type(e).__name__
        if name in ("IpBlocked", "RequestBlocked"):
            raise Blocked(name)
        return None
    lines = []
    for s in tr:
        t = int(s.start)
        lines.append(f"[{t // 60}:{t % 60:02d}] {s.text.replace(chr(10), ' ')}")
    return "\n".join(lines)


def sync(settings: dict, universe: dict, lister: Callable = ytdlp_list, info: Callable = ytdlp_info,
         transcript: Callable = fetch_transcript, sleep: Callable = time.sleep) -> dict:
    cfg = settings["videos"]
    limit = int(cfg.get("max_new_per_run", 25))
    tdir = paths()["data_dir"] / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    names = {f["ticker"]: sorted({f["ticker"].split(".")[0].split("-")[0], *f.get("aliases", [])}) for f in universe["funds"]}
    stats = {"listed": 0, "new": 0, "dated": 0, "transcripts": 0, "blocked": False}
    listing = lister(cfg["channel_url"])
    stats["listed"] = len(listing)
    with db.session() as con:
        for e in listing:
            cur = con.execute("INSERT OR IGNORE INTO videos(id,title,views,duration,source,tickers) VALUES (?,?,?,?,?,?)",
                              (e["id"], e["title"], e.get("views"), e.get("duration"), "sync", "{}"))
            stats["new"] += cur.rowcount
            if e.get("views") is not None:
                con.execute("UPDATE videos SET views=? WHERE id=?", (e["views"], e["id"]))
    order = {e["id"]: i for i, e in enumerate(listing)}  # channel order is newest first
    with db.session() as con:
        undated = [r["id"] for r in con.execute("SELECT id FROM videos WHERE date IS NULL AND est_date IS NULL")]
    for vid in sorted(undated, key=lambda x: order.get(x, 10**6))[:limit]:
        try:
            i = info(vid)
        except Exception as ex:
            log.warning("info failed for %s: %s", vid, ex)
            continue
        with db.session() as con:
            con.execute("UPDATE videos SET date=COALESCE(?,date), views=COALESCE(?,views), duration=COALESCE(?,duration) WHERE id=?",
                        (i.get("date"), i.get("views"), i.get("duration"), vid))
        stats["dated"] += 1
        sleep(1.0)
    if cfg.get("fetch_transcripts", True):
        with db.session() as con:
            todo = [dict(r) for r in con.execute("SELECT id,date FROM videos WHERE has_transcript=0 AND date IS NOT NULL ORDER BY date DESC LIMIT ?", (limit,))]
        for v in todo:
            f = tdir / f"{v['id']}.txt"
            try:
                text = f.read_text(encoding="utf-8") if f.exists() else transcript(v["id"])
            except Blocked:
                stats["blocked"] = True
                log.warning("transcript fetch blocked by YouTube; will retry next run")
                break
            if not text:
                continue
            f.write_text(text, encoding="utf-8")
            found = find_mentions(text, names)
            with db.session() as con:
                con.execute("UPDATE videos SET has_transcript=1, tickers=? WHERE id=?",
                            (json.dumps({k: x["n"] for k, x in found.items()}), v["id"]))
                con.execute("DELETE FROM mentions WHERE video_id=?", (v["id"],))
                for tk, x in found.items():
                    for s in x["snippets"][:1]:
                        con.execute("INSERT INTO mentions(ticker,video_id,date,t,snippet,n) VALUES (?,?,?,?,?,?)",
                                    (tk, v["id"], v["date"], s["t"], s["snippet"], x["n"]))
            stats["transcripts"] += 1
            sleep(2.0)
    db.bump()
    return stats
