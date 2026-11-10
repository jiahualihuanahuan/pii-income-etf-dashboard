import json

from app import db, jobs, videos
from app.config import load_settings, load_universe

TX = "[0:10] AAA is my favorite fund right now.\n[0:30] I also like BBB."


def test_video_sync_adds_dates_transcripts_and_mentions(env):
    jobs.bootstrap()
    calls = {"info": 0, "tr": 0}

    def lister(url):
        return [{"id": "new1", "title": "New ETF video", "views": 1000, "duration": 600}, {"id": "v2", "title": "New video", "views": 25000}]

    def info(vid):
        calls["info"] += 1
        return {"date": "2026-11-08", "views": 1200, "duration": 601}

    def tr(vid):
        calls["tr"] += 1
        return TX

    res = videos.sync(load_settings(), load_universe(), lister, info, tr, sleep=lambda s: None)
    assert res["new"] == 1 and res["dated"] == 1 and res["transcripts"] >= 1 and not res["blocked"]
    with db.session() as con:
        v = con.execute("SELECT * FROM videos WHERE id='new1'").fetchone()
        assert v["date"] == "2026-11-08" and v["has_transcript"] == 1
        assert json.loads(v["tickers"]) == {"AAA.TO": 1, "BBB.TO": 1}
        assert con.execute("SELECT views FROM videos WHERE id='v2'").fetchone()["views"] == 25000
    # second run is idempotent for already-dated videos
    res2 = videos.sync(load_settings(), load_universe(), lister, info, tr, sleep=lambda s: None)
    assert res2["new"] == 0 and res2["dated"] == 0 and calls["info"] == 1


def test_video_sync_stops_when_blocked(env):
    jobs.bootstrap()

    def tr(vid):
        raise videos.Blocked("IpBlocked")

    res = videos.sync(load_settings(), load_universe(), lambda u: [], lambda v: {}, tr, sleep=lambda s: None)
    assert res["blocked"] is True and res["transcripts"] == 0
