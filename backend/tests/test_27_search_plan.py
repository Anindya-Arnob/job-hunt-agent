"""The search plan: each site's number of searches (and how many are narrower city searches), shared fairly
between places and job fields unless you give some more weight; warnings when a site may block; and each
site's jobs saved to the board as soon as that site finishes."""
import asyncio
from collections import Counter

from sqlalchemy import text
from sqlmodel import select

from jobagent import db, models as m
from jobagent.agents.site_agent import SearchOutcome, SiteResult
from jobagent.graphs import search_graph as G
from jobagent.schemas import RawJob
from jobagent.steps import planner as P, search_plan as SP

ROLES = ["Data Science", "Data Engineering"]
JT = ["Internship", "Working Student", "Part-time"]
PLACES = ["Berlin", "Germany", "Europe", "United Kingdom"]


def test_searches_are_shared_equally_between_places_and_fields():
    qs = P.plan_site_queries("wellfound", ROLES, JT, PLACES, 5, max_queries=24)
    assert len(qs) == 24
    assert set(Counter(q.place for q in qs).values()) == {6}           # 4 places x 6
    assert set(Counter(q.role for q in qs).values()) == {12}           # 2 fields x 12
    # a few searches still cover every place and field (round-robin)
    first = qs[:8]
    assert {q.place for q in first} == set(PLACES) and {q.role for q in first} == set(ROLES)


def test_more_weight_gets_more_searches():
    plan = {"places": {"Berlin": 3}, "fields": {"Data Science": 2}}
    qs = P.plan_site_queries("wellfound", ROLES, JT, PLACES, 5, max_queries=30, plan=plan)
    by_place = Counter(q.place for q in qs)
    assert by_place["Berlin"] > 2 * by_place["United Kingdom"]
    in_berlin = Counter(q.role for q in qs if q.place == "Berlin")
    assert in_berlin["Data Science"] >= in_berlin["Data Engineering"]


def test_few_searches_still_reach_every_place_and_field():
    roles = list(P.ROLE_TERMS)
    places = ["Berlin", "Germany", "Europe", "United Kingdom", "United States", "Canada", "Australia", "New Zealand", "Remote"]
    qs = P.plan_site_queries("glassdoor", roles, JT, places, 5, max_queries=20, plan={"places": {"Berlin": 3}})
    by_place, by_field = Counter(q.place for q in qs), Counter(q.role for q in qs)
    assert set(by_place) == set(places) and by_place["Berlin"] >= 3 * min(by_place.values())   # Most = triple, not all
    assert set(by_field) == set(roles) and max(by_field.values()) - min(by_field.values()) <= 1


def test_site_numbers_and_their_limits():
    assert P.site_plan(None, "linkedin")["searches"] == 80 and P.site_plan(None, "linkedin")["narrow"] == 15
    sp = P.site_plan({"sites": {"linkedin": {"searches": 5000, "narrow": 9000}}}, "linkedin")
    assert sp["searches"] == P.MAX_SEARCHES and sp["narrow"] == sp["searches"]
    sp = P.site_plan({"sites": {"xing": {"searches": "abc"}}}, "xing")
    assert sp["searches"] == 120


def test_preview_counts_and_warnings():
    form = {"roles": ROLES, "job_types": JT, "locations": PLACES + ["New Zealand"], "days": 5,
            "sources": ["linkedin", "stepstone"], "plan": {"sites": {"linkedin": {"searches": 120, "narrow": 20}}}}
    pv = SP.preview(form)
    li = next(s for s in pv["sites"] if s["site"] == "linkedin")
    assert li["risky"] and any(w["level"] == "risk" and "too many requests" in w["text"] for w in li["warnings"])
    assert sum(li["places"].values()) == li["used"] == min(100, li["possible"]) and li["narrow"] == 20 and li["minutes"] > 0
    assert any(w["level"] == "info" for w in li["warnings"])          # fewer different searches than allowed
    st = next(s for s in pv["sites"] if s["site"] == "stepstone")
    assert "United Kingdom" in st["not_here"] and "United Kingdom" not in st["places"]     # StepStone: Germany only
    # too few searches for every place: the screen says which ones get none
    form["plan"] = {"sites": {"linkedin": {"searches": 4, "narrow": 0}}}      # 5 places, 4 searches
    li = next(s for s in SP.preview(form)["sites"] if s["site"] == "linkedin")
    gap = [w["text"] for w in li["warnings"] if w["level"] == "gap"]
    assert gap and "New Zealand" in gap[0] and not li["risky"]


class FakeAgent:
    """Every search says it has more jobs than the agent reads."""
    seen: list = []

    def __init__(self, site, **kw):
        self.site = site
        self.refuses_automation = self.human_check_given_up = self.blocked_given_up = False
        self.start_mode = "guest"

    async def run(self, queries):
        FakeAgent.seen.append(len(queries))
        return SiteResult(site=self.site, outcomes=[SearchOutcome(query=q, status="limit", reported=500 + i, collected=100)
                                                    for i, q in enumerate(queries)])


def _profile(plan=None, sources=("linkedin",)):
    with db.session_scope() as s:
        fp = m.FilterProfile(name="p", roles=ROLES, job_types=JT, locations=["Germany"], work_modes=[], days=5,
                             sources=list(sources), plan=plan or {})
        s.add(fp)
        s.flush()
        return fp.id


def test_narrower_searches_stop_at_your_number(monkeypatch):
    monkeypatch.setattr(G, "SiteAgent", FakeAgent)
    FakeAgent.seen = []
    fid = _profile({"sites": {"linkedin": {"searches": 10, "narrow": 4}}})
    with db.session_scope() as s:
        fp = s.get(m.FilterProfile, fid)
    qs = P.plan_for_profile("linkedin", fp)
    assert len(qs) == 6
    out = asyncio.run(G.site_agent({"site": "linkedin", "run_id": 1, "filter_profile_id": fid,
                                    "queries": [q.model_dump() for q in qs]}))
    assert FakeAgent.seen == [6, 4]                                   # 6 planned, then exactly 4 narrower
    with db.session_scope() as s:
        stats = s.exec(select(m.SearchStat).where(m.SearchStat.run_id == 1)).all()
    assert sum(1 for x in stats if x.status == "split") >= 1 and len(stats) == 10
    assert out["saved"] and out["raw_jobs"] == []


def test_a_finished_site_is_on_the_board_straight_away(monkeypatch):
    class OneJob(FakeAgent):
        async def run(self, queries):
            job = RawJob(source="linkedin", title="Werkstudent Data Science", company="Quickco GmbH", location="Berlin",
                         url="https://www.linkedin.com/jobs/view/quick-1", description="Python, SQL, pandas. " * 30)
            return SiteResult(site=self.site, jobs=[job],
                              outcomes=[SearchOutcome(query=queries[0], status="ok", collected=1, pages=1)])
    monkeypatch.setattr(G, "SiteAgent", OneJob)
    fid = _profile()
    with db.session_scope() as s:
        run = m.Run(kind="search", status="running")
        s.add(run)
        s.flush()
        rid = run.id
    q = P.plan_site_queries("linkedin", ROLES, JT, ["Berlin"], 5, max_queries=1)[0]
    out = asyncio.run(G.site_agent({"site": "linkedin", "run_id": rid, "filter_profile_id": fid,
                                    "queries": [q.model_dump()]}))
    with db.session_scope() as s:              # while the run is still going (other sites searching)
        jobs = s.exec(select(m.Job).where(m.Job.company == "Quickco GmbH")).all()
        assert len(jobs) == 1 and jobs[0].first_seen_run == rid and jobs[0].role_category      # sorted already
    assert out["raw_jobs"] == [] and out["saved"][0]["new"] == 1
    # the step at the end adds up every site's numbers (and stores nothing twice)
    res = G.clean_dedupe({"run_id": rid, "filter_profile_id": fid, "raw_jobs": [], "outcomes": [],
                          "saved": out["saved"] + [{"found": 5, "new": 2, "scored": 2}]})
    assert res["counts"]["found"] == 6 and res["counts"]["new"] == 3 and res["new_ids"] == []


def test_older_database_gets_the_plan_column():
    with db.get_engine().begin() as con:
        con.execute(text("ALTER TABLE filter_profile DROP COLUMN plan"))
    db._add_missing_columns()
    with db.get_engine().begin() as con:
        cols = {r[1] for r in con.execute(text("PRAGMA table_info(filter_profile)"))}
    assert "plan" in cols


def test_plan_api():
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    with TestClient(app) as c:
        body = {"name": "x", "roles": ROLES, "job_types": JT, "locations": PLACES, "sources": ["linkedin", "xing"],
                "plan": {"sites": {"xing": {"searches": 40, "narrow": 10}}, "places": {"Berlin": 2}}}
        pv = c.post("/api/filters/plan", json=body).json()
        xing = next(s for s in pv["sites"] if s["site"] == "xing")
        assert xing["searches"] == 40 and xing["base"] == 30 and pv["weights"]["places"]["Berlin"] == 2
        saved = c.post("/api/filters", json=body).json()
        assert saved["plan"]["sites"]["xing"]["searches"] == 40
        assert any(f["id"] == saved["id"] and f["plan"]["places"]["Berlin"] == 2 for f in c.get("/api/filters").json())
