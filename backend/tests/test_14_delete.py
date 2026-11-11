from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

from jobagent import db, models as m
from jobagent.schemas import RawJob
from jobagent.steps.clean import ingest, known_url_keys


def _seed(tmp_path):
    folder = tmp_path / "Job" / "Claude CV" / "Acme"
    folder.mkdir(parents=True)
    cv = folder / "cv.docx"
    cv.write_text("x")
    pdf = folder / "cv.pdf"
    pdf.write_text("x")
    fp = m.FilterProfile(name="t", locations=["Berlin"], days=5)
    raws = [RawJob(source="linkedin", title=f"Werkstudent Data {i}", company=f"Acme {i}", location="Berlin",
                   url=f"https://www.linkedin.com/jobs/view/41000000{i:02d}", description="Python") for i in range(3)]
    ids = ingest(raws, 1, fp)["new_ids"]
    with db.session_scope() as s:
        s.add(m.Score(job_id=ids[0], total=70, verdict="Apply"))
        s.add(m.Document(job_id=ids[0], kind="cv", docx_path=str(cv), pdf_path=str(pdf)))
        s.add(m.Application(job_id=ids[0], notes="x"))
    return ids, raws, fp, folder


def test_delete_jobs_tombstone_and_files(tmp_path):
    from jobagent.api.main import app
    ids, raws, fp, folder = _seed(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/jobs/delete", json={"job_ids": [ids[0], ids[1]], "delete_files": True}).json()
        assert r == {"deleted": 2, "files_deleted": 2}
        assert not folder.exists()                                  # empty company folder removed
        with db.session_scope() as s:
            assert {j.id for j in s.exec(select(m.Job)).all()} == {ids[2]}
            assert not s.exec(select(m.Score)).all() and not s.exec(select(m.Document)).all()
            assert not s.exec(select(m.Application)).all()
        # deleted jobs never come back in later searches, and their pages are not fetched again
        again = ingest(raws, 2, fp)
        assert again["new"] == 0 and again["previously_deleted"] == 2 and again["duplicates"] == 1
        assert "linkedin.com/jobs/view/4100000000" in known_url_keys()
        # single delete without files; then "forget" a deletion so it can be found again
        assert c.delete(f"/api/jobs/{ids[2]}").json()["deleted"] == 1
        assert c.delete(f"/api/jobs/{ids[2]}").status_code == 404
        dead = c.get("/api/jobs/deleted").json()
        assert len(dead) == 3
        c.delete(f"/api/jobs/deleted/{dead[0]['id']}")
        assert ingest(raws, 3, fp)["new"] == 1


def test_forget_flag_allows_refind(tmp_path):
    from jobagent.steps.clean import delete_jobs
    ids, raws, fp, folder = _seed(tmp_path)
    delete_jobs([ids[1]], forget=True)
    assert ingest(raws, 2, fp)["new"] == 1
    assert (folder / "cv.docx").exists()                            # files kept by default
