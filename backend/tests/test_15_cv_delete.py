import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from jobagent import db, models as m
from jobagent.cv import service as CV

from profile_fixture import sample_profile


def test_delete_versions_and_cvs_permanently(master_cv_path):
    draft = CV.upload(master_cv_path)
    a = CV.confirm(draft["profile"], draft["source_file"], name="Old CV")
    upload_file = draft["source_file"]
    p = sample_profile().model_dump()
    v2 = CV.save_version(a["profile_id"], p, note="v2")
    # current version can't be deleted, older one can
    with pytest.raises(CV.CVError):
        CV.delete_version(v2["version_id"])
    CV.delete_version(a["version_id"])
    assert [v["version"] for v in CV.list_profiles()[0]["versions"]] == [2]
    from pathlib import Path
    assert not Path(upload_file).exists()                    # uploaded original removed with its version
    # main CV can't be deleted; after another becomes main it can, without the 30-day wait
    b = CV.confirm(sample_profile().model_dump(), name="New CV", make_main=False)
    with pytest.raises(CV.CVError):
        CV.delete_permanently(a["profile_id"])
    CV.set_main(b["profile_id"])
    CV.delete_permanently(a["profile_id"])
    assert [r["name"] for r in CV.list_profiles()] == ["New CV"]
    with db.session_scope() as s:
        assert len(s.exec(select(m.CVVersion)).all()) == 1


def test_delete_tailored_documents(tmp_path):
    from jobagent.api.main import app
    folder = tmp_path / "Job" / "Claude CV" / "Acme"
    folder.mkdir(parents=True)
    files = [folder / n for n in ("cv.docx", "cv.pdf", "letter.docx", "letter.pdf")]
    for f in files:
        f.write_text("x")
    with db.session_scope() as s:
        j = m.Job(title="x", company="Acme", status="built")
        s.add(j)
        s.flush()
        jid = j.id
        s.add(m.Document(job_id=jid, kind="cv", docx_path=str(files[0]), pdf_path=str(files[1])))
        s.add(m.Document(job_id=jid, kind="letter", docx_path=str(files[2]), pdf_path=str(files[3])))
    with TestClient(app) as c:
        r = c.delete(f"/api/documents/job/{jid}").json()
        assert r["files_deleted"] == 4 and not folder.exists()
        assert c.get(f"/api/jobs/{jid}").json()["status"] == "scored"
        assert c.delete(f"/api/documents/job/{jid}").status_code == 404
        a = CV.confirm(sample_profile().model_dump(), name="A")
        b = CV.confirm(sample_profile().model_dump(), name="B", make_main=False)
        assert c.delete(f"/api/cv/{a['profile_id']}/permanent").status_code == 400     # main
        assert c.delete(f"/api/cv/{b['profile_id']}/permanent").json()["ok"]
        assert c.delete(f"/api/cv/versions/{a['version_id']}").status_code == 400      # current version
