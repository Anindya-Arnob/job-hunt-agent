"""Reach your ATS target: the missing words worth the most points, proof from your CV or GitHub pre-ticked, what
you tick written into your main CV once, then the CV is built again."""
import asyncio
import time

import pytest

from jobagent import db, models as m
from jobagent.config import settings
from jobagent.cv import service as CV
from jobagent.docs.pdf import find_soffice
from jobagent.graphs import build_graph as B
from jobagent.steps import boost

from test_10_build_graph import setup  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
EXTRA = (" You build production LLM services with automated tests, error handling, monitoring and cost control, "
         "work directly with customers and analyse business processes. Fluent German (C1) required.")


@pytest.fixture
def built(setup, monkeypatch):  # noqa: F811
    with db.session_scope() as s:
        j = s.get(m.Job, setup["good"])
        j.full_description = (j.full_description or "") + EXTRA
        s.add(j)
    monkeypatch.setattr(settings, "github_projects", False)
    asyncio.run(B.start_build([setup["good"]], force=[setup["good"]]))   # it has a German knock-out
    return setup["good"]


def test_the_list_reaches_the_target_with_the_fewest_words(built, monkeypatch):
    p = boost.plan(built, target=99)
    assert p["score"] < 99 and p["need"] == 99 - p["score"]
    terms = [i["term"] for i in p["items"]]
    assert "Error Handling" in terms + [i["term"] for i in p["others"]]
    pts = [i["points"] for i in p["items"]]
    assert pts == sorted(pts, reverse=True)
    assert all(not t.lower().startswith("german") for t in terms)            # a language is never tickable
    assert any("German" in k for k in p["knockouts"])
    if p["reachable"]:
        assert sum(pts) >= p["need"] and sum(pts[:-1]) < p["need"]           # just enough words, not all
    small = boost.plan(built, target=p["score"] + 1)
    assert len(small["items"]) <= len(p["items"])
    assert boost.plan(built, target=p["score"])["items"] == []


def test_proof_from_your_main_cv_is_found(built):
    prof, vid = CV.get_main()
    prof.projects[0].bullets.append("Added retries, timeouts and fallbacks around every model call.")
    with db.session_scope() as s:
        pid = s.get(m.CVVersion, vid).profile_id
    CV.save_version(pid, prof.model_dump(), "test")
    items = boost.plan(built, target=100)
    every = items["items"] + items["others"]
    eh = next(i for i in every if i["term"] == "Error Handling")
    assert eh["proof"] and eh["proof"]["where"] == prof.projects[0].name and "retries" in eh["proof"]["text"]
    mon = next(i for i in every if i["term"] == "Monitoring")
    assert mon["proof"] is None


def test_ticked_words_go_into_your_main_cv_once(built):
    prof, _ = CV.get_main()
    proj = prof.projects[0].name
    out = boost.add_to_main([
        {"term": "Error Handling", "where": f"{proj}: added retries and timeouts to the API calls"},
        {"term": "Monitoring", "where": ""},
    ], company="Nordlicht AI GmbH")
    after, _ = CV.get_main()
    skills = [i for g in after.skills for i in g.items]
    assert "Error Handling" in skills and "Monitoring" in skills
    p0 = next(p for p in after.projects if p.name == proj)
    assert any("retries" in b.lower() for b in p0.bullets)                   # written from your words
    assert not any(ch.isdigit() for b in p0.bullets[len(prof.projects[0].bullets):] for ch in b)   # no made-up numbers
    again = boost.add_to_main([{"term": "Error Handling", "where": ""}])
    after2, _ = CV.get_main()
    assert [i for g in after2.skills for i in g.items].count("Error Handling") == 1
    assert out["version_id"] and again["version_id"] != out["version_id"]


def test_add_and_rebuild_lifts_the_score(built):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    with TestClient(app) as c:
        before = c.get(f"/api/jobs/{built}/boost", params={"target": 100}).json()
        pick = [i for i in before["items"] + before["others"] if i["term"] in ("Error Handling", "Monitoring")]
        r = c.post(f"/api/jobs/{built}/boost", json={"items": [{"term": i["term"], "where": "", "proof": i["proof"]} for i in pick]})
        assert r.status_code == 200 and r.json()["run_id"]
        for _ in range(120):
            if not c.get("/api/build/active").json():
                break
            time.sleep(0.5)
        after = c.get(f"/api/jobs/{built}/boost", params={"target": 100}).json()
        assert after["score"] > before["score"], (before["score"], after["score"])
        left = [i["term"] for i in after["items"] + after["others"]]
        assert "Error Handling" not in left and "Monitoring" not in left
        assert c.post(f"/api/jobs/{built}/boost", json={"items": []}).status_code == 400
