"""Stopped builds get their own Job board tab; the Apply list shows every built CV, newest first, with its ATS
score, and a job you won't apply to can be taken off the list (and put back)."""
import asyncio
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from jobagent import db, models as m
from jobagent.apply import session as AS

from test_10_build_graph import setup  # noqa: F401  (fixture)


def test_a_stopped_build_is_listed_until_the_cv_is_built(setup):  # noqa: F811
    from jobagent.api.main import app
    from jobagent.graphs import build_graph as B
    jid = setup["good"]
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        j.full_description += " Fluent German (C1) is required."
        s.add(j)
    with TestClient(app) as c:
        assert c.get("/api/jobs/counts").json()["stopped"] == 0
        r = c.post("/api/build", json={"job_ids": [jid]}).json()          # stopped before the build starts
        assert r["stopped"]
        rows = c.get("/api/jobs?tab=stopped").json()
        assert [x["id"] for x in rows] == [jid] and "German" in rows[0]["stopped"]["reasons"][0] and rows[0]["stopped"]["at"]
        assert c.get("/api/jobs/counts").json()["stopped"] == 1
        assert c.get("/api/jobs?tab=new").json()                          # still a job without a CV
        out = asyncio.run(B.start_build([jid], force=[jid]))               # "Build anyway"
        assert out["results"][0]["status"] == "built"
        assert c.get("/api/jobs?tab=stopped").json() == [] and c.get("/api/jobs/counts").json()["stopped"] == 0


def test_a_stop_inside_the_build_is_listed_and_deleting_the_job_clears_it(setup):  # noqa: F811
    from jobagent.api.main import app
    from jobagent.graphs import build_graph as B
    jid = setup["good"]
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        j.full_description += " At least 5 years of experience."
        s.add(j)
    assert asyncio.run(B.start_build([jid]))["results"][0]["status"] == "stopped"
    with TestClient(app) as c:
        assert [x["id"] for x in c.get("/api/jobs?tab=stopped").json()] == [jid]
        assert c.post("/api/jobs/delete", json={"job_ids": [jid]}).status_code == 200
        assert c.get("/api/jobs/counts").json()["stopped"] == 0
    with db.session_scope() as s:
        assert s.get(m.AppSetting, f"stopped:{jid}") is None


def _built(tmp_path, n=3):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ids = []
    now = datetime.now().astimezone()
    with db.session_scope() as s:
        for i in range(n):
            j = m.Job(title=f"Job {i}", company=f"Firma {i}", links=[f"https://x.example/{i}"], status=m.JobStatus.BUILT,
                      url_key=f"k{i}", dedupe_key=f"k{i}")
            s.add(j)
            s.flush()
            ids.append(j.id)
            s.add(m.Score(job_id=j.id, total=90 - 10 * i, skills=30, experience=20, title_fit=8, education=8, language=10,
                          type_location=5, verdict="Apply"))
            s.add(m.Document(job_id=j.id, kind="cv", pdf_path=str(pdf), built_at=now - timedelta(days=3 - i),
                             content={"_ats": {"after": {"score": 70 + 10 * i}}}))
    return ids


def test_every_built_cv_newest_first_with_its_ats_score(tmp_path):
    from jobagent.config import settings
    ids = _built(tmp_path, 3)
    q = AS.queue(include_all=True)
    assert [x["id"] for x in q] == ids[::-1]                                # newest build first
    assert q[0]["ats"] == 90 and q[0]["built_at"] and q[0]["score"] == 70
    old = settings.apply_daily_limit
    try:
        settings.apply_daily_limit = 1
        assert len(AS.queue(include_all=True)) == 3                         # the daily limit hides nothing
    finally:
        settings.apply_daily_limit = old


def test_a_cv_without_pdf_is_still_listed(tmp_path):
    ids = _built(tmp_path, 1)
    docx = tmp_path / "cv.docx"
    docx.write_bytes(b"x")
    from sqlmodel import select
    with db.session_scope() as s:
        d = s.exec(select(m.Document).where(m.Document.job_id == ids[0])).first()
        d.pdf_path, d.docx_path = "", str(docx)
        s.add(d)
    q = AS.queue(include_all=True)
    assert q[0]["pdf_missing"] and q[0]["cv"].endswith("cv.docx")
    assert AS.queue() == []                                                 # but not offered to the form filler


def test_not_applying_and_undo(tmp_path):
    from jobagent.api.main import app
    ids = _built(tmp_path, 2)
    with TestClient(app) as c:
        assert c.post(f"/api/apply/jobs/{ids[0]}/not-applying").status_code == 200
        q = {x["id"]: x for x in c.get("/api/apply/queue?all=true").json()["jobs"]}
        assert q[ids[0]]["not_applying"] and not q[ids[1]]["not_applying"]
        assert ids[0] not in {x["id"] for x in c.get("/api/apply/queue").json()["jobs"]}
        ex = c.get("/api/jobs?tab=excluded").json()
        assert ex[0]["id"] == ids[0] and ex[0]["exclude_reason"] == "Not applying"
        assert c.post(f"/api/apply/jobs/{ids[0]}/not-applying?undo=true").status_code == 200
        q = {x["id"]: x for x in c.get("/api/apply/queue?all=true").json()["jobs"]}
        assert not q[ids[0]]["not_applying"]
        with db.session_scope() as s:
            assert s.get(m.Job, ids[0]).status == "built"
        assert c.post("/api/apply/jobs/99999/not-applying").status_code == 404
