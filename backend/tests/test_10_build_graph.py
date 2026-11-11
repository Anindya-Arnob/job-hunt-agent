import asyncio
from pathlib import Path

import openpyxl
import pytest
from sqlmodel import select

from jobagent import db, models as m
from jobagent.config import settings
from jobagent.cv import service as CV
from jobagent.docs.pdf import find_soffice
from jobagent.graphs import build_graph as B
from jobagent.steps import write as W
from jobagent.steps.score import score_job, store_score

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")

DESC_GOOD = ("Working student Machine Learning, 20 hours per week. Fine-tune LLMs with Hugging Face Transformers, LoRA, "
             "PEFT. Build RAG pipelines with LangChain. Python, PyTorch, Docker, SQL. English required.")


@pytest.fixture
def setup(master_cv_path):
    draft = CV.upload(master_cv_path)
    r = CV.confirm(draft["profile"], draft["source_file"])
    prof, vid = CV.get_main()
    ids = {}
    with db.session_scope() as s:
        for key, company, title, desc, loc in [
            ("good", "Nordlicht AI GmbH", "Working Student Machine Learning (m/w/d)", DESC_GOOD, "Berlin, Germany"),
            ("accent", "Société Générale", "Stage – Data Science (H/F)", DESC_GOOD + " Internship, 6 months.", "Paris, France"),
            ("low", "Kiez Retail", "Werkstudent Buchhaltung Data", "Excel, DATEV, accounting, SAP FI.", "Berlin"),
        ]:
            j = m.Job(company=company, title=title, full_description=desc, location=loc, links=[f"https://x/{key}"],
                      job_type="Internship" if key == "accent" else "Working Student", status="scored",
                      keywords=["LLM", "RAG", "PyTorch"] if key != "low" else ["DATEV", "SAP"])
            s.add(j)
            s.flush()
            ids[key] = j.id
    for key, jid in ids.items():
        with db.session_scope() as s:
            job = s.get(m.Job, jid)
        res = score_job(job, prof)
        if key == "low":
            res.skills, res.experience, res.title_fit = 5, 5, 3
        store_score(jid, res, vid)
    return ids


def test_build_selected_jobs(setup):
    ids = setup
    out = asyncio.run(B.start_build([ids["good"], ids["accent"], ids["low"]]))
    res = {r["job_id"]: r for r in out["results"]}
    assert res[ids["low"]]["status"] == "skipped" and "below 40" in res[ids["low"]]["reason"]
    good = res[ids["good"]]
    assert good["status"] == "built", good["failed_checks"]
    folder = Path(good["folder"])
    assert folder.name == "Nordlicht AI GmbH"
    for f in ("Alex_Morgan_CV_Nordlicht_AI_GmbH_Working_Student_Machine_Learning",
              "Alex_Morgan_Cover_Letter_Nordlicht_AI_GmbH_Working_Student_Machine_Learning"):
        assert (folder / f"{f}.docx").exists() and (folder / f"{f}.pdf").exists()
    acc = res[ids["accent"]]
    assert Path(acc["folder"]).name == "Société Générale"
    assert acc["cv_file"] == "Alex_Morgan_CV_Société_Générale_Stage_–_Data_Science_(H_F).docx"
    assert any("Paris" in b or "relocation" in b for b in acc["blockers"])   # honest blocker in the letter
    with db.session_scope() as s:
        docs = s.exec(select(m.Document).where(m.Document.job_id == ids["good"])).all()
        assert {d.kind for d in docs} == {"cv", "letter"} and all(d.qa_passed for d in docs)
        assert s.get(m.Job, ids["good"]).status == "built"
    wb = openpyxl.load_workbook(out["report_path"])
    ws = wb.active
    assert ws.cell(1, 1).value == "Score" and ws.max_row == 4
    statuses = {ws.cell(r, 2).value: ws.cell(r, 7).value for r in range(2, 5)}
    assert statuses["Kiez Retail"].startswith("skipped")

    # second time: not overwritten
    out2 = asyncio.run(B.start_build([ids["good"]]))
    assert out2["results"][0]["status"] == "already built"
    # explicit rebuild
    out3 = asyncio.run(B.start_build([ids["good"]], rebuild=True))
    assert out3["results"][0]["status"] == "built"


def test_pause_for_review_then_resume(setup):
    ids = setup
    out = asyncio.run(B.start_build(None))
    assert out["paused"] is True
    with db.session_scope() as s:
        run = s.get(m.Run, out["run_id"])
        assert run.status == "paused"
    cands = {c["job_id"] for c in B.candidates()}
    assert ids["good"] in cands and ids["low"] not in cands
    out2 = asyncio.run(B.resume_build(out["run_id"], [ids["good"]]))
    assert out2["paused"] is False and out2["results"][0]["status"] == "built"
    with db.session_scope() as s:
        assert s.get(m.Run, out["run_id"]).status == "done"


def test_failed_checks_retry_then_flag(setup, monkeypatch):
    ids = setup
    calls = {"n": 0}
    real = W.write_cv

    def bad_writer(job, prof, issues=None, level=0, **kw):
        calls["n"] += 1
        cv = real(job, prof, issues, level, **kw)
        cv.skills[0].items.append("Kubernetes")     # a tool you don't have
        return cv
    monkeypatch.setattr(W, "write_cv", bad_writer)
    out = asyncio.run(B.start_build([ids["good"]]))
    r = out["results"][0]
    assert calls["n"] == settings.max_build_attempts
    assert r["status"] == "built - needs your attention"
    assert any("Only tools" in f and "Kubernetes" in f for f in r["failed_checks"])


def test_second_build_starts_at_the_length_that_fitted(setup):
    ids = setup
    first = asyncio.run(B.start_build([ids["good"]]))
    with db.session_scope() as s:
        doc = s.exec(select(m.Document).where(m.Document.job_id == ids["good"], m.Document.kind == "cv")).one()
        first_attempts = doc.attempts
    from jobagent import events
    msgs = [e["message"] for e in events.recent(first["run_id"]) if "CV attempt" in e.get("message", "")]
    if first_attempts > 1:
        assert "too long for one page" in msgs[0] and "rewriting" in msgs[0]
    second = asyncio.run(B.start_build([ids["accent"]]))
    with db.session_scope() as s:
        doc2 = s.exec(select(m.Document).where(m.Document.job_id == ids["accent"], m.Document.kind == "cv")).one()
    assert doc2.attempts <= 2 and doc2.qa_passed
    _ = second
