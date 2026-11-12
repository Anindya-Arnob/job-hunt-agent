"""The progress bar on the Search screen: how far each source is and about how long the run still takes."""
import asyncio

from jobagent import progress
from jobagent.graphs import search_graph as G

from test_07_search_graph import env  # noqa: F401  (fixture)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_time_left_from_measured_speed(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(progress.time, "time", clock)
    monkeypatch.setattr(progress.settings, "page_delay_seconds", 2.0)    # a job page: about 4.8 s
    monkeypatch.setattr(progress.settings, "max_details_per_site", 300)
    rid = 990001
    progress.start(rid, {"linkedin": 100, "xing": 10, "feeds": 0}, no_pages=("feeds",))
    snap = progress.snapshot(rid)
    assert snap["eta_seconds"] is None and snap["fraction"] == 0      # nothing measured yet: "estimating"

    progress.source(rid, "linkedin", phase="searching", searched=0, searches=100, new_seen=0)
    progress.source(rid, "xing", phase="searching", searched=0, searches=10, new_seen=0)
    clock.t += 100
    progress.source(rid, "linkedin", searched=10, new_seen=50)           # 10 s per search, 5 new jobs per search
    progress.source(rid, "xing", searched=10, new_seen=4, done=False)
    snap = progress.snapshot(rid)
    li = next(s for s in snap["sources"] if s["key"] == "linkedin")
    # 90 searches x 10 s + min(300, 500 projected new) x 4.8 s
    assert abs(li["eta_seconds"] - (900 + 300 * 4.8)) < 2
    assert li["what"] == "search 10 of 100 · 50 new so far" and li["fraction"] == 0.05
    assert snap["eta_seconds"] >= li["eta_seconds"]                     # the slowest source decides
    assert 0 < snap["fraction"] < 0.1

    # LinkedIn reads its job pages: the measured page speed replaces the guess
    progress.source(rid, "linkedin", phase="searching", searched=100, new_seen=400)
    clock.t += 900
    progress.source(rid, "linkedin", phase="reading", pages_done=0, pages_total=300)
    clock.t += 60
    progress.source(rid, "linkedin", phase="reading", pages_done=20, pages_total=300)   # 3 s per page
    li = next(s for s in progress.snapshot(rid)["sources"] if s["key"] == "linkedin")
    assert li["eta_seconds"] == 280 * 3 and li["what"] == "reading job pages 20 of 300"
    assert 0.5 < li["fraction"] < 0.6

    for k in ("linkedin", "xing", "feeds"):
        progress.source(rid, k, done=True, jobs=7)
    progress.post(rid, "scoring", 10, 40)
    snap = progress.snapshot(rid)
    assert snap["stage"] == "scoring" and snap["eta_seconds"] is not None and snap["fraction"] < 1
    progress.finish(rid)
    snap = progress.snapshot(rid)
    assert snap["stage"] == "done" and snap["fraction"] == 1 and snap["eta_seconds"] == 0
    progress.finish(rid, "cancelled")                                   # a late Stop does not undo "done"
    assert progress.snapshot(rid)["stage"] == "done"


def test_a_real_run_reports_progress(env, monkeypatch):  # noqa: F811
    fs, fid = env
    orig = G.planner.plan_site_queries
    monkeypatch.setattr(G.planner, "plan_site_queries", lambda *a, **k: orig(*a, **k)[:2])
    monkeypatch.setattr(G.planner, "plan_feed_queries", lambda *a, **k: [{"keywords": "Werkstudent Data Science", "location": "Berlin"}])
    seen = []
    monkeypatch.setattr(progress, "_push", lambda rid, force=False: seen.append(progress.snapshot(rid)))
    final = asyncio.run(G.run_search(fid))
    rid = final["run_id"]
    snap = progress.snapshot(rid)
    assert snap["stage"] == "done" and snap["fraction"] == 1
    keys = {s["key"] for s in snap["sources"]}
    assert {"linkedin", "glassdoor", "xing", "feeds"} <= keys
    assert all(s["phase"] == "done" for s in snap["sources"])
    # the screen got updates while it ran: searching, reading job pages, then storing/sorting/scoring
    whats = " | ".join(s["what"] for e in seen for s in e["sources"])
    assert "search 1 of 2" in whats or "search 2 of 2" in whats
    assert "reading job pages" in whats
    stages = [e["stage"] for e in seen]
    assert "searching" in stages and "scoring" in stages and stages[-1] == "done"
    assert any(e["eta_seconds"] is not None for e in seen if e["stage"] == "searching")

    # and the API gives the same thing
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    with TestClient(app) as c:
        r = c.get(f"/api/runs/{rid}/progress").json()
        assert r["stage"] == "done" and r["sources"]
        assert c.get("/api/runs/999999/progress").status_code == 404
