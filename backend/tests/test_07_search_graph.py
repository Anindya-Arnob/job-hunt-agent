import asyncio
from datetime import datetime, timedelta

import httpx
import openpyxl
import pytest
from sqlmodel import select

from jobagent import db, events, models as m
from jobagent.agents import sites as S
from jobagent.cv import service as CV
from jobagent.graphs import search_graph as G

from fake_sites import FakeReader, FakeSites

RECENT = (datetime.now() - timedelta(days=1)).isoformat()


def feed_handler(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "greenhouse" in u:
        return httpx.Response(200, json={"jobs": [
            {"title": "Working Student Machine Learning", "location": {"name": "Berlin"}, "absolute_url": "https://gh.example/acme/1",
             "first_published": RECENT, "content": "Working student, 16-20 hours. PyTorch, Python, LangChain, RAG. English."}]})
    if "arbeitsagentur" in u and "/jobdetails/" in u:
        return httpx.Response(200, json={"stellenangebotsBeschreibung": "Werkstudent Data Science. Python, SQL, Power BI."})
    if "arbeitsagentur" in u:
        return httpx.Response(200, json={"maxErgebnisse": 1, "stellenangebote": [
            {"refnr": "10000-1", "titel": "Werkstudent Data Science (m/w/d)", "arbeitgeber": "Acme Analytics GmbH",
             "arbeitsort": {"ort": "Berlin", "land": "Deutschland"}, "aktuelleVeroeffentlichungsdatum": RECENT[:10]}]})
    return httpx.Response(404)


@pytest.fixture
def env(monkeypatch, master_cv_path):
    with FakeSites() as fs:
        for site, prefix in [("linkedin", "/li"), ("glassdoor", "/gd"), ("xing", "/xg")]:
            monkeypatch.setattr(S.SITES[site], "base", fs.base + prefix)
        monkeypatch.setattr(G, "READER_FACTORY", lambda s, mode: FakeReader(s, mode, fs.base))
        monkeypatch.setattr(G, "FEED_TRANSPORT", httpx.MockTransport(feed_handler))
        draft = CV.upload(master_cv_path)
        CV.confirm(draft["profile"], draft["source_file"])
        from jobagent.browser import connections
        connections.set_status("glassdoor", "connected")
        with db.session_scope() as s:
            s.add(m.TargetCompany(name="Acme AI", ats="greenhouse", board_id="acme"))
            fp = m.FilterProfile(name="test", roles=["Data Science", "Machine Learning"],
                                 job_types=["Internship", "Working Student", "Part-time"], locations=["Berlin", "Remote"],
                                 work_modes=["Remote", "Hybrid", "On-site"], days=5,
                                 sources=["linkedin", "glassdoor", "xing", "ats_feeds", "arbeitsagentur"], language_max="any")
            s.add(fp)
            s.flush()
            fid = fp.id
        yield fs, fid


def test_full_search_run(env, monkeypatch):
    fs, fid = env
    # keep the run small: 1 query per site
    orig = G.planner.plan_site_queries
    monkeypatch.setattr(G.planner, "plan_site_queries", lambda *a, **k: orig(*a, **k)[:1])
    monkeypatch.setattr(G.planner, "plan_feed_queries", lambda *a, **k: [{"keywords": "Werkstudent Data Science", "location": "Berlin"}])
    final = asyncio.run(G.run_search(fid))
    run_id = final["run_id"]
    c = final["counts"]
    assert c["found"] > 30 and c["new"] > 0 and c["duplicates"] >= 1
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job)).all()
        run = s.get(m.Run, run_id)
        stats = s.exec(select(m.SearchStat).where(m.SearchStat.run_id == run_id)).all()
    # Arbeitsagentur job merged with the LinkedIn one (same company, title, city)
    acme = [j for j in jobs if j.company.startswith("Acme Analytics")]
    assert len(acme) == 1 and set(acme[0].sources) == {"linkedin", "arbeitsagentur"}
    # LinkedIn senior role excluded before its description was fetched
    senior = [j for j in jobs if j.title == "Senior Data Engineer"][0]
    assert senior.status == "excluded" and senior.full_description == ""
    # old posting excluded with a reason
    old = [j for j in jobs if j.title == "Data Analyst Part-time"][0]
    assert old.status == "excluded" and "more than 5 days" in old.exclude_reason
    # German C1 job is scored but flagged as a hard deal-breaker -> Skip
    with db.session_scope() as s:
        genai = s.exec(select(m.Job).where(m.Job.title == "Working Student GenAI / LLM")).one()
        sc = s.exec(select(m.Score).where(m.Score.job_id == genai.id)).one()
    assert sc.verdict == "Skip" and any(d["hard"] for d in sc.deal_breakers)
    # Glassdoor used login fallback; greenhouse feed job included
    gd = [st for st in stats if st.site == "glassdoor"][0]
    assert gd.mode == "login" and gd.status == "ok"
    assert any(j.sources == ["ats_feeds"] for j in jobs)
    assert run.status == "done" and run.counts["scored"] > 0 and "by_source" in run.counts
    # Excel written
    wb = openpyxl.load_workbook(final["export_path"])
    assert wb["Jobs"].max_row - 1 == len([j for j in jobs if j.first_seen_run == run_id and j.status == "scored"])
    assert any(e["type"] == "run_done" for e in events.recent(run_id))

    # second run: nothing new, no descriptions refetched
    hits_before = len([h for h in fs.hits if "jobPosting" in h])
    final2 = asyncio.run(G.run_search(fid))
    assert final2["counts"]["new"] == 0
    assert len([h for h in fs.hits if "jobPosting" in h]) == hits_before


def test_resume_after_crash(env, monkeypatch):
    fs, fid = env
    orig = G.planner.plan_site_queries
    monkeypatch.setattr(G.planner, "plan_site_queries", lambda *a, **k: orig(*a, **k)[:1])
    calls = {"n": 0}
    real_export = G.export

    def flaky_export(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("power cut")
        return real_export(state)

    monkeypatch.setattr(G, "export", flaky_export)
    with pytest.raises(RuntimeError):
        asyncio.run(G.run_search(fid))
    with db.session_scope() as s:
        run = s.exec(select(m.Run)).all()[-1]
    assert run.status == "failed"
    hits = len(fs.hits)
    final = asyncio.run(G.run_search(fid, run_id=run.id, resume=True))
    assert final["export_path"] and len(fs.hits) == hits      # no site visited again
    with db.session_scope() as s:
        assert s.get(m.Run, run.id).status == "done"


def test_no_main_cv_skips_scoring(monkeypatch):
    with db.session_scope() as s:
        fp = m.FilterProfile(name="feeds only", roles=["Data Science"], job_types=["Working Student"],
                             locations=["Berlin"], days=5, sources=["arbeitsagentur"])
        s.add(fp)
        s.flush()
        fid = fp.id
    monkeypatch.setattr(G, "FEED_TRANSPORT", httpx.MockTransport(feed_handler))
    final = asyncio.run(G.run_search(fid))
    assert final["counts"]["scored"] == 0 and any("No main CV" in e for e in final["errors"])
