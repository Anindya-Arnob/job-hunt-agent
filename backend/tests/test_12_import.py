import shutil
from pathlib import Path

import pytest
from sqlmodel import select

from jobagent import db, models as m
from jobagent.config import settings
from jobagent.importer import import_previous
from jobagent.steps.clean import ingest, known_url_keys
from jobagent.schemas import RawJob

UP = Path("/mnt/user-data/uploads/Job")


@pytest.mark.skipif(not (UP / "Claude CV" / "_Report_2026-11-03.xlsx").exists(), reason="real report files not available")
def test_import_real_previous_files():
    settings.xl_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(UP / "XL" / "Job_Match_Summary_2026-11-02.xlsx", settings.xl_dir)
    shutil.copy(UP / "Claude CV" / "_Report_2026-11-03.xlsx", settings.cv_out_dir)
    # two of the CVs named in the report exist on disk; "CV built" must come from real files only
    for folder, stem in (("Micron", "Alex_Morgan_CV_Micron_Intern_-_Data_Science"),
                         ("Light", "Alex_Morgan_CV_Light_AI_Engineering_Intern")):
        d = settings.cv_out_dir / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}.pdf").write_text("x")
        (d / f"{stem.replace('_CV_', '_Cover_Letter_')}.docx").write_text("x")
    res = {r["file"]: r for r in import_previous()}
    assert res["Job_Match_Summary_2026-11-02.xlsx"]["imported"] > 300
    rep = res["_Report_2026-11-03.xlsx"]
    assert rep["updated"] > 300                       # same jobs, now marked built
    with db.session_scope() as s:
        built = s.exec(select(m.Job).where(m.Job.status == "built")).all()
        scores = s.exec(select(m.Score)).all()
    assert sorted(j.company for j in built) == ["Light", "Micron"]
    assert len(scores) > 300 and all(sc.is_outdated for sc in scores)
    built = s.exec(select(m.Job)).all() if False else built
    keys = known_url_keys()
    assert len(keys) > 300
    # a job from the old files found again later is a duplicate, not new
    any_job = built[0]
    fp = m.FilterProfile(name="x", locations=["Berlin"], days=5)
    c = ingest([RawJob(source="linkedin", title=any_job.title, company=any_job.company, location=any_job.location,
                       url=any_job.link)], 99, fp)
    assert c["new"] == 0 and c["duplicates"] == 1
    # importing twice changes nothing
    again = {r["file"]: r for r in import_previous()}
    assert again["Job_Match_Summary_2026-11-02.xlsx"]["imported"] == 0
