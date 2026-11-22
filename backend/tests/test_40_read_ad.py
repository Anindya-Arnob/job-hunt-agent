"""Claude reads the ad once, at Build CV: a must-have tool missing from your CV stops the build, the ATS score
uses Claude's list, and the words it found teach the free word matcher."""
import asyncio

import pytest

from jobagent import db, llm, models as m, vocab
from jobagent.steps import adread as A
from jobagent.steps import knockouts as K

from test_10_build_graph import setup  # noqa: F401  (fixture)

SAP = """What you bring

• Currently enrolled in Computer Science, Mathematics, Physics, or Operations Research
• Strong programming skills in Python and working knowledge of at least one optimization framework (Gurobi, CPLEX, Xpress, or OR-Tools) — required
• Solid foundations in at least one of: mathematical optimization (LP/MIP, heuristics), machine learning, or algorithm design
• Ability to design and run reproducible experiments and document findings clearly
• Nice to have: GPU programming (CUDA, cuOpt), PyTorch, C++.

What You'll Build

Classical Optimization. Mathematical programming (MIP), heuristics and metaheuristics for supply chain planning.

Additional Information

About SAP

As a leader in enterprise applications and business AI, SAP stands at the nexus of business and technology.

We win with inclusion

SAP's culture of inclusion, focus on health and well-being, and flexible working models help ensure that everyone – regardless of background – feels included.

AI Usage in the Recruitment Process

Please note that any violation of these guidelines may result in disqualification.

#Workingstudent #Werkstudent #Germany #Deutschland #Optimization"""

READ = A.AdRead(
    required=[A.Group(any_of=["Python"], must=True),
              A.Group(any_of=["Gurobi", "CPLEX", "Xpress", "OR-Tools"], must=True),
              A.Group(any_of=["Mathematical Optimization", "Machine Learning", "Algorithm Design"])],
    nice_to_have=["CUDA", "cuOpt", "PyTorch", "C++"],
    ways_of_working=["Reproducible Experiments", "Documentation"],
    spellings=[A.Spelling(term="OR-Tools", also=["Google OR-Tools"]),
               A.Spelling(term="Mathematical Optimization", also=["mathematical programming"])])


@pytest.fixture
def claude_reads(monkeypatch):
    """Claude Code 'reads' the ad: the read_ad call answers READ, every other call stays offline."""
    calls = []
    real = llm.structured
    monkeypatch.setattr(A, "can_read", lambda: True)

    def fake(node, schema, system, user, **kw):
        if node == "read_ad":
            calls.append(user)
            return READ.model_copy(deep=True)
        return real(node, schema, system, user, **kw)
    monkeypatch.setattr(llm, "structured", fake)
    yield calls
    vocab.set_learned({})


def _sap_job(setup):  # noqa: F811
    jid = setup["good"]
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        j.company, j.title, j.full_description = "SAP", "Working Student AI-Enhanced Optimization", SAP
        s.add(j)
    return jid


def test_the_company_text_is_cut_before_claude_reads():
    t = A.trim(SAP)
    assert "OR-Tools" in t and "Nice to have" in t and "Classical Optimization" in t
    assert "nexus of business" not in t and "regardless of background" not in t and "disqualification" not in t
    assert len(t) < len(SAP) * 0.75
    assert "About the job" in A.trim("About the job\n\nWe build agents.\n\nYour profile\n\nPython")


def test_hashtag_deutschland_is_not_a_german_requirement():
    from jobagent.steps.classify import heuristic_classification
    assert "German" not in heuristic_classification("Working Student", SAP).language_requirements
    assert "German" in heuristic_classification("Werkstudent", "Deutschkenntnisse von Vorteil").language_requirements


def test_read_once_and_kept(setup, claude_reads):  # noqa: F811
    jid = _sap_job(setup)
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
    ad, by_claude = A.read(job)
    assert by_claude and ad.required[1].must
    assert "About SAP" not in claude_reads[0] and "CV" not in claude_reads[0].split("AD:")[0]     # only the ad
    A.read(job)
    assert len(claude_reads) == 1                                               # a rebuild does not read again
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        assert "Gurobi or CPLEX or Xpress or OR-Tools" in j.required_skills and "CUDA" in j.keywords


def test_without_claude_the_word_list_stands_in_and_is_not_kept(setup):  # noqa: F811
    jid = _sap_job(setup)
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
    ad, by_claude = A.read(job)
    assert not by_claude and A.cached(jid) is None and not A.must_groups(ad)


def test_a_missing_must_have_tool_stops_the_build(setup, claude_reads):  # noqa: F811
    from jobagent.graphs import build_graph as B
    jid = _sap_job(setup)
    out = asyncio.run(B.start_build([jid]))
    r = out["results"][0]
    assert r["status"] == "stopped" and "Gurobi, CPLEX, Xpress, OR-Tools" in r["reason"]
    assert len(claude_reads) == 1
    out = asyncio.run(B.start_build([jid], force=[jid]))                 # "Build anyway"
    assert out["results"][0]["status"] == "built" and len(claude_reads) == 1


def test_one_of_the_alternatives_is_enough(setup, claude_reads):  # noqa: F811
    from jobagent.cv import service as CV
    jid = _sap_job(setup)
    prof, _ = CV.get_main()
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
    A.read(job)
    assert K.missing_tools(job, prof) == [["Gurobi", "CPLEX", "Xpress", "OR-Tools"]]
    p2 = prof.model_copy(deep=True)
    p2.skills[0].items.append("Google OR-Tools")                       # another spelling counts
    assert K.missing_tools(job, p2) == [] and not K.for_job(job, p2)


def test_ats_and_writer_use_claudes_list(setup, claude_reads):  # noqa: F811
    from jobagent.docs import ats
    from jobagent.graphs.build_graph import _load, job_for_writing
    from jobagent.steps.write import _job_block
    jid = _sap_job(setup)
    job, sc = _load(jid)
    A.read(job)
    job, sc = _load(jid)
    jw = job_for_writing(job, sc)
    kw = ats.keywords(jw)
    assert kw["Gurobi or CPLEX or Xpress or OR-Tools"] == 3 and kw["CUDA"] == 2
    assert jw.must_have and "Reproducible Experiments" in jw.ways_of_working
    block = _job_block(jw)
    assert "Must have" in block and "never in the skills list" in block and "nexus of business" not in block
    assert ats.hit("Gurobi or CPLEX or Xpress or OR-Tools", "Solved routing with OR-Tools")


def test_claudes_words_teach_the_free_matcher(setup, claude_reads):  # noqa: F811
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    jid = _sap_job(setup)
    assert "Gurobi" not in vocab.find_terms("Experience with Gurobi and CUDA")
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
    A.read(job)
    words = A.learned()
    assert {"Gurobi", "CPLEX", "OR-Tools", "CUDA", "cuOpt"} <= set(words)
    assert "Python" not in words and "Reproducible Experiments" not in words     # known / a way of working
    assert "SAP" in words["Gurobi"]["from"]
    assert {"Gurobi", "CUDA"} <= set(vocab.find_terms("Experience with Gurobi and CUDA"))
    assert "Google OR-Tools" in vocab.variants("OR-Tools")
    A.load_learned()                                      # what the app does at start
    assert "Gurobi" in vocab.SKILLS
    with TestClient(app) as c:
        got = c.get("/api/learned-words").json()
        assert any(w["word"] == "OR-Tools" and "Google OR-Tools" in w["also"] for w in got)
        assert c.post("/api/learned-words/forget", json={"word": "Gurobi"}).status_code == 200
        assert c.post("/api/learned-words/forget", json={"word": "Gurobi"}).status_code == 404
    assert "Gurobi" not in vocab.SKILLS and "Gurobi" not in vocab.find_terms("Experience with Gurobi")
    assert "CUDA" in vocab.SKILLS


def test_i_have_this_adds_it_and_builds(setup, claude_reads):  # noqa: F811
    import time
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    from jobagent.cv import service as CV
    jid = _sap_job(setup)
    with TestClient(app) as c:
        r = c.post("/api/build", json={"job_ids": [jid]}).json()
        for _ in range(120):
            if not c.get("/api/build/active").json():
                break
            time.sleep(0.25)
        d = c.get(f"/api/jobs/{jid}").json()
        assert d["read_by_claude"] and d["missing_tools"] == [["Gurobi", "CPLEX", "Xpress", "OR-Tools"]]
        assert any("Required" in k for k in d["knockouts"])
        r = c.post(f"/api/jobs/{jid}/boost", json={"force": False, "items": [
            {"term": "Gurobi or CPLEX or Xpress or OR-Tools", "where": "Thesis: vehicle routing with OR-Tools"}]}).json()
        assert r["added"][0]["term"] == "OR-Tools" and r["run_id"] and not r["stopped"]
        for _ in range(120):
            if not c.get("/api/build/active").json():
                break
            time.sleep(0.25)
        assert not c.get(f"/api/jobs/{jid}").json()["missing_tools"]
        assert c.get(f"/api/jobs/{jid}").json()["status"] == "built"
        assert c.get("/api/jobs/counts").json()["stopped"] == 0, c.get("/api/jobs?tab=stopped").json()
    prof, _ = CV.get_main()
    assert "OR-Tools" in [i for g in prof.skills for i in g.items]


def test_claude_check_reads_only_and_shows_the_score_change(setup, claude_reads, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    from jobagent.steps import adread
    jid = _sap_job(setup)
    with TestClient(app) as c:
        before = c.get(f"/api/jobs/{jid}").json()["score"]
        r = c.post(f"/api/jobs/{jid}/claude-check").json()
        assert r["read_by_claude"] and r["must_have"] == [["Python"], ["Gurobi", "CPLEX", "Xpress", "OR-Tools"]]
        d = c.get(f"/api/jobs/{jid}").json()
        assert d["claude_check"]["before"] == before and d["claude_check"]["after"] == d["score"] < before
        assert d["read_by_claude"] and d["missing_tools"] and not d["documents"]          # nothing built
        assert len(claude_reads) == 1
        c.post(f"/api/jobs/{jid}/claude-check?again=true")                          # read again on purpose
        assert len(claude_reads) == 2
        assert c.get(f"/api/jobs/{jid}").json()["claude_check"]["before"] == before    # still the first score
        r = c.post("/api/build", json={"job_ids": [jid]}).json()                       # read already: stops at once
        assert r["run_id"] is None and "Gurobi" in r["stopped"][0]["reasons"][0]
        assert c.post("/api/jobs/99999/claude-check").status_code == 404
        monkeypatch.setattr(adread, "can_read", lambda: False)
        assert "not logged in" in c.post(f"/api/jobs/{jid}/claude-check").json()["detail"]


def _wait_check(c):
    import time
    for _ in range(200):
        if not c.get("/api/claude-check/active").json():
            return
        time.sleep(0.1)
    raise AssertionError("Claude check did not finish")


def test_claude_check_for_the_ticked_jobs_one_after_another(setup, claude_reads):  # noqa: F811
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    jid = _sap_job(setup)
    other = setup["accent"]
    with TestClient(app) as c:
        r = c.post("/api/claude-check", json={"job_ids": [jid, other]}).json()
        assert r["run_id"] and r["total"] == 2 and r["skipped"] == 0
        _wait_check(c)
        assert len(claude_reads) == 2
        for j in (jid, other):
            d = c.get(f"/api/jobs/{j}").json()
            assert d["read_by_claude"] and d["claude_check"] and not d["documents"]       # read, nothing built
        run = c.get(f"/api/runs/{r['run_id']}").json()
        run = run.get("run", run)
        assert run["status"] == "done" and run["counts"]["checked"] == 2
        r = c.post("/api/claude-check", json={"job_ids": [jid, other]}).json()   # read already: no tokens
        assert r["run_id"] is None and r["skipped"] == 2 and len(claude_reads) == 2
        assert c.post("/api/claude-check", json={"job_ids": []}).status_code == 400


def test_claude_check_stops_when_claude_cannot_read(setup, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    monkeypatch.setattr(A, "can_read", lambda: True)          # looks logged in, but every read falls back
    with TestClient(app) as c:
        r = c.post("/api/claude-check", json={"job_ids": [setup["good"], setup["accent"]]}).json()
        _wait_check(c)
        run = c.get(f"/api/runs/{r['run_id']}").json()
        run = run.get("run", run)
        assert run["status"] == "failed" and run["counts"]["checked"] == 0 and "could not read" in run["errors"][0]
