"""Building CVs without burning tokens: a CV that is only too long is shortened by the app (no new Claude
rewrite), and the same job is never built twice at the same time (the Apply screen lost track of a running
build after you left it, so pressing Rebuild again started a second one)."""
import asyncio

import pytest
from sqlmodel import select

from jobagent import db, models as m
from jobagent.docs.pdf import find_soffice
from jobagent.graphs import build_graph as B
from jobagent.schemas import CVContent
from jobagent.steps import write as W

from test_10_build_graph import setup  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")


def test_trim_cuts_the_weakest_bullets_and_keeps_every_item():
    cv = CVContent(tagline="t", summary="s", skills=[{"category": "Tools", "items": [f"Tool{i}" for i in range(9)]}],
                   experience=[{"title": "Intern", "bullets": ["Built RAG with LangChain", "Made coffee", "Wrote PyTorch code"]}],
                   projects=[{"name": "P1", "bullets": ["LLM fine-tuning with LoRA", "Cleaned the garage"]},
                             {"name": "P2", "bullets": ["RAG demo"]}],
                   education=[{"degree": "M.Sc."}])
    kw = {"RAG": 3, "LangChain": 2, "PyTorch": 2, "LoRA": 2, "LLM": 3}
    made = B._trim_once(cv, kw, 2, 2)
    assert made == 2
    assert "Made coffee" not in cv.experience[0].bullets and "Cleaned the garage" not in cv.projects[0].bullets
    assert all(len(p.bullets) >= 1 for p in cv.projects) and len(cv.experience[0].bullets) >= 2
    # nothing left to cut from the bullets: the longest skill group gives up its least useful items
    assert B._trim_once(cv, kw, 3, 2) == 3 and len(cv.skills[0].items) == 6


def test_a_too_long_cv_is_shortened_without_a_rewrite(setup, monkeypatch):  # noqa: F811
    calls = []
    real = W.write_cv

    def long_writer(*a, **kw):
        calls.append(1)
        cv = real(*a, **kw)
        for it in cv.experience + cv.projects:          # far too much text for one page
            it.bullets = it.bullets + [f"Extra detail {w} about the work done in this item, written at length "
                                       f"to fill the page with more words than fit" for w in "abcdef"]
        return cv
    monkeypatch.setattr(W, "write_cv", long_writer)
    out = asyncio.run(B.start_build([setup["good"]], rebuild=True))
    with db.session_scope() as s:
        doc = s.exec(select(m.Document).where(m.Document.job_id == setup["good"], m.Document.kind == "cv")).first()
    msgs = [e["message"] for e in B.events.recent(out["run_id"], 500) if "Shortened the CV" in str(e.get("message", ""))]
    assert len(calls) == 1, "one Claude call only: the app cut the extra lines itself"
    assert doc and next(c for c in doc.qa_results if c["name"] == "Exactly 1 page")["passed"]
    assert msgs


def test_the_same_job_is_not_built_twice_at_once(setup, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from jobagent.api import runs
    from jobagent.api.main import app
    gate = {}

    async def slow_build(job_ids, cv_profile_id, rebuild, run_id):
        gate.setdefault("started", []).append(job_ids)
        await asyncio.sleep(1.5)
        return {"run_id": run_id, "results": []}
    monkeypatch.setattr(runs.build_graph, "start_build", slow_build)
    with TestClient(app) as c:
        a = c.post("/api/build", json={"job_ids": [setup["good"]], "rebuild": True}).json()
        active = c.get("/api/build/active").json()
        assert [b["job_id"] for b in active] == [setup["good"]] and active[0]["title"].startswith("Working Student")
        b = c.post("/api/build", json={"job_ids": [setup["good"]], "rebuild": True}).json()
        assert b["run_id"] == a["run_id"] and b["already_building"] == [setup["good"]]
        both = c.post("/api/build", json={"job_ids": [setup["good"], setup["accent"]], "rebuild": True}).json()
        assert both["already_building"] == [setup["good"]] and both["run_id"] != a["run_id"]
        import time
        for _ in range(30):
            if len(gate.get("started", [])) == 2:
                break
            time.sleep(0.05)
        assert gate["started"] == [[setup["good"]], [setup["accent"]]]       # each job once
        for _ in range(40):
            if not c.get("/api/build/active").json():
                break
            time.sleep(0.1)
        assert c.get("/api/build/active").json() == []


def test_stop_ends_the_running_claude_call_at_once():
    import threading
    import time
    from jobagent import llm
    res = {}

    def work():
        llm.set_current_run(9901)
        try:
            llm._run_claude(["sleep", "30"], "prompt", "/tmp", timeout=60)
        except llm.LLMUnavailable as e:
            res["err"] = str(e)
    t = threading.Thread(target=work)
    t0 = time.time()
    t.start()
    time.sleep(0.5)
    assert llm.kill_run(9901) == 1
    t.join(10)
    assert res.get("err") == "Stopped by you" and time.time() - t0 < 5


def test_stop_button_clears_the_build(setup, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from jobagent.api import runs
    from jobagent.api.main import app

    async def slow_build(job_ids, cv_profile_id, rebuild, run_id):
        await asyncio.sleep(30)
    monkeypatch.setattr(runs.build_graph, "start_build", slow_build)
    with TestClient(app) as c:
        r = c.post("/api/build", json={"job_ids": [setup["good"]], "rebuild": True}).json()
        assert c.get("/api/build/active").json()[0]["in_run"] == 1
        c.post(f"/api/runs/{r['run_id']}/cancel")
        assert c.get("/api/build/active").json() == []
        assert c.get(f"/api/runs/{r['run_id']}").json()["run"]["status"] == "cancelled"


def test_no_gender_tags_in_letters_or_form_answers():
    from jobagent.apply.draft import _parse_answers
    from jobagent.textutil import strip_gender
    for raw, clean in [("Praktikant (m/w/d) im Bereich KI", "Praktikant im Bereich KI"),
                       ("JUNIOR GRAPH AI ENGINEER (M/F/D)", "JUNIOR GRAPH AI ENGINEER"),
                       ("Werkstudent (w/m/div.) Data", "Werkstudent Data"), ("Job (gn*)", "Job"),
                       ("Job m/w/d in Berlin", "Job in Berlin"), ("Stage Data (H/F)", "Stage Data"),
                       ("Python/SQL and B2B/B2C", "Python/SQL and B2B/B2C")]:
        assert strip_gender(raw) == clean, raw
    ans = _parse_answers('{"answers": {"q1": "I am applying for the Praktikant (m/w/d) role at Marquardt.", "q2": null}}')
    assert ans == {"q1": "I am applying for the Praktikant role at Marquardt.", "q2": None}


def test_claude_thinks_briefly_unless_you_choose_more(monkeypatch):
    """Measured on a real build: without a level Claude Code thought for 12,000-17,000 tokens per CV."""
    import json
    from jobagent import claude_code, llm
    from jobagent.config import settings
    from jobagent.schemas import CheckResult
    from test_23_cv_options import HELP
    seen = {}

    class Out:
        stdout = json.dumps({"result": json.dumps({"passed": True, "issues": []}), "usage": {}}).encode()
        stderr = b""
    monkeypatch.setattr(llm, "_run_claude", lambda cmd, prompt, cwd, timeout=420: (seen.setdefault("cmds", []).append(cmd), Out())[1])
    monkeypatch.setattr(claude_code, "exe", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "help_text", lambda e=None: HELP)
    monkeypatch.setattr(claude_code, "installed_plugins", lambda: {})
    for chosen, want in (("", "low"), ("high", "high")):
        monkeypatch.setattr(settings, "cv_effort", chosen)
        llm._cli_structured("write_cv", CheckResult, "S", "U")
        cmd = seen["cmds"][-1]
        assert cmd[cmd.index("--effort") + 1] == want
    llm._cli_structured("mail_jobs", CheckResult, "S", "U")
    assert "--effort" not in seen["cmds"][-1]                         # other calls unchanged


def test_a_reviewer_fix_that_breaks_a_check_does_not_start_more_rewrites(setup, monkeypatch):  # noqa: F811
    from jobagent.config import settings
    from jobagent.schemas import ReviewResult
    monkeypatch.setattr(settings, "recruiter_review", True)
    calls = []
    real = W.write_cv

    def writer(*a, **kw):
        calls.append(1)
        cv = real(*a, **kw)
        if len(calls) > 1:                  # the version after the review names a tool the main CV does not have
            cv.skills[0].items.append("Kubernetes Operator SDK")
        return cv
    monkeypatch.setattr(W, "write_cv", writer)
    monkeypatch.setattr(W, "review_cv", lambda *a, **k: ReviewResult(verdict="maybe", reason="ok",
                                                                    fixes=["Shorten the tagline"]))
    out = asyncio.run(B.start_build([setup["good"]], rebuild=True))
    msgs = [e["message"] for e in B.events.recent(out["run_id"], 500)]
    assert len(calls) == 2, msgs                                    # first version + one try with the fixes
    assert any("keeping the version that passed every check" in mm for mm in msgs)
    with db.session_scope() as s:
        doc = s.exec(select(m.Document).where(m.Document.job_id == setup["good"], m.Document.kind == "cv")).first()
    assert doc.qa_passed and "Kubernetes Operator SDK" not in str(doc.content)


def test_letter_may_name_numbers_from_the_job_ad(setup):  # noqa: F811
    """Seen on a real build: '5 Monate' and '4.000' from the ad made the letter fail and be written twice."""
    from jobagent.cv import service as CVS
    from jobagent.docs import qa
    from jobagent.schemas import LetterContent
    prof, _ = CVS.get_main()
    L = LetterContent(recipient="MAHLE", subject="Bewerbung",
                      paragraphs=["Das Praktikum über 5 Monate bei einem Team von 4.000 Menschen passt zu mir.",
                                  "Ich habe 37 Modelle gebaut.", "Mit freundlichen Grüßen"])
    ad = "Praktikum für 5 Monate. Rund 4.000 Mitarbeitende weltweit."
    nums = next(c for c in qa.check_letter(L, prof, None, {}, job_text=ad) if c.name.startswith("Only numbers"))
    assert not nums.passed and nums.detail == "37"                  # from the ad: fine; invented: still caught


def test_tool_named_in_your_cv_text_is_yours():
    """Seen on a real build: 'tools not on your CV (Large Language Models)' vs 'Keeps the keywords your main CV
    had (put back: Large Language Models)' - three rewrites going back and forth."""
    from jobagent.docs import qa
    from jobagent.schemas import CVProfile
    prof = CVProfile(name="A", summary="M.Sc. student building Large Language Models and RAG systems.",
                     skills=[{"category": "ML", "items": ["PyTorch"]}])
    cv = CVContent(tagline="t", summary="s", skills=[{"category": "ML", "items": ["PyTorch", "Large Language Models (LLMs)",
                                                                               "Large Language Models", "Kubernetes"]}],
                   experience=[], projects=[{"name": "p", "bullets": ["x"]}], education=[])
    assert qa.unknown_tools(cv, prof) == ["Kubernetes"]


def test_letter_language_is_your_choice_and_english_by_default(monkeypatch):
    from jobagent import llm
    from jobagent.config import settings
    from jobagent.schemas import JobForWriting
    seen = {}
    real = llm.structured

    def spy(node, schema, system, user, **kw):
        seen["system"], seen["user"] = system, user
        return real(node, schema, system, user, **kw)
    monkeypatch.setattr(llm, "structured", spy)
    from jobagent.cv import service as CVS
    from jobagent.schemas import CVProfile
    prof = CVProfile(name="A B", summary="Student.", skills=[{"category": "ML", "items": ["Python"]}],
                     projects=[{"name": "P", "bullets": ["x"]}])
    cv = CVContent(tagline="t", summary="s", skills=[{"category": "ML", "items": ["Python"]}], experience=[],
                   projects=[{"name": "P", "bullets": ["x"]}], education=[])
    job = JobForWriting(company="MAHLE", title="Praktikum Machine Learning", job_type="Full-time",
                        description="Wir suchen dich für unser Team in Stuttgart.")
    W.write_letter(job, prof, cv)
    assert "LETTER LANGUAGE: English" in seen["user"] and "Same language as the job ad" not in seen["system"]
    assert "I am happy to discuss a start date." in seen["user"]          # full-time: no 'working student' line
    monkeypatch.setattr(settings, "letter_language", "ad")
    W.write_letter(job, prof, cv)
    assert "LETTER LANGUAGE: the language of the job ad" in seen["user"]
    del CVS
