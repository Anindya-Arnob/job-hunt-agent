from sqlmodel import select

from jobagent import db, models as m
from jobagent.config import settings
from jobagent.sync_docs import sync_all


def mk(folder, name):
    d = settings.cv_out_dir / folder
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("x")
    return p


def test_status_follows_files_on_disk():
    with db.session_scope() as s:
        a = m.Job(company="Perplexity", title="Internship - Machine Learning Research Engineer", status="scored")
        b = m.Job(company="Perplexity", title="Internship - Search Machine Learning Engineer", status="scored")
        c = m.Job(company="OKAPI:Orbits GmbH", title="Werkstudent / Praktikum AI & Automatisierung (m/w/d)", status="built")
        d = m.Job(company="JawBuddy", title="Quality & Data Intern (m/f/d) – Mandatory Internship", status="built")
        e = m.Job(company="Artiquare GmbH", title="AI/ML Development Intern", status="scored")
        s.add_all([a, b, c, d, e])
        s.flush()
        ids = [x.id for x in (a, b, c, d, e)]
    mk("Perplexity", "Alex_Morgan_CV_Perplexity_Internship_ML_Research_Engineer.pdf")
    mk("Perplexity", "Alex_Morgan_CV_Perplexity_Internship_-_Search_Machine_Learning_Engineer.pdf")
    mk("Perplexity", "Alex_Morgan_Cover_Letter_Perplexity_Internship_-_Search_Machine_Learning_Engineer.pdf")
    mk("OKAPIOrbits GmbH", "Alex_Morgan_CV_OKAPIOrbits_GmbH_Werkstudent_Praktikum_AI_&_Automatisierung_(mwd).docx")
    mk("JawBuddy", "Alex_Morgan_CV_JawBuddy_Working_Student_Quality_and_Data.docx")
    cv = mk("Artiquare GmbH", "Alex_Morgan_CV_Artiquare_GmbH_AIML_Development_Intern.docx")
    r = sync_all()
    assert sync_all()["linked"] == 0                               # settles in one pass
    with db.session_scope() as s:
        st = {j.id: j.status for j in s.exec(select(m.Job)).all()}
        docs = {(x.job_id, x.kind): x for x in s.exec(select(m.Document)).all()}
    assert st[ids[0]] == st[ids[1]] == st[ids[2]] == st[ids[4]] == "built"
    assert "Research" in docs[(ids[0], "cv")].pdf_path and "Search" in docs[(ids[1], "cv")].pdf_path   # no mix-up
    assert (ids[1], "letter") in docs and (ids[0], "letter") not in docs
    assert st[ids[3]] == "scored"           # "built" in the old sheet, but the file is for another JawBuddy job
    # you delete a CV in Explorer -> the job is no longer "CV built"
    cv.unlink()
    sync_all()
    with db.session_scope() as s:
        assert s.get(m.Job, ids[4]).status == "scored"
        assert not s.exec(select(m.Document).where(m.Document.job_id == ids[4])).all()
    _ = r


def test_old_versions_of_a_job_are_moved_not_deleted(tmp_path):
    from jobagent.config import settings
    from jobagent.sync_docs import sync_all
    folder = settings.cv_out_dir / "NVIDIA"
    folder.mkdir(parents=True)
    new = {k: folder / f"Alex_Morgan_{k}_NVIDIA_Relational_Foundation_Model_Intern,_Modern_Data_St.pdf" for k in ("CV", "Cover_Letter")}
    old = {k: folder / f"Alex_Morgan_{k}_NVIDIA_Relational_Foundation_Model_Intern,_Modern_Data_Stack_–_2027.pdf" for k in ("CV", "Cover_Letter")}
    other = folder / "Alex_Morgan_CV_NVIDIA_Image_and_Data_Processing_Libraries_Intern.pdf"
    for p in list(new.values()) + list(old.values()) + [other]:
        p.write_bytes(b"%PDF")
    with db.session_scope() as s:
        j = m.Job(title="Relational Foundation Model Intern, Modern Data Stack – 2027", company="NVIDIA",
                  status=m.JobStatus.BUILT, url_key="n1", dedupe_key="n1")
        j2 = m.Job(title="Image and Data Processing Libraries Intern", company="NVIDIA", status=m.JobStatus.SCORED,
                   url_key="n2", dedupe_key="n2")
        s.add(j); s.add(j2); s.flush()
        s.add(m.Document(job_id=j.id, kind="cv", pdf_path=str(new["CV"])))
        s.add(m.Document(job_id=j.id, kind="letter", pdf_path=str(new["Cover_Letter"])))
    r = sync_all()
    assert r["retired"] == 2
    assert all(p.exists() for p in new.values()) and other.exists()          # the other job's CV is untouched
    assert not any(p.exists() for p in old.values())
    assert (settings.cv_out_dir / "_old_versions" / "NVIDIA" / old["CV"].name).exists()
