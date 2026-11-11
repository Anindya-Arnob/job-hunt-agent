from datetime import date

import openpyxl
from sqlmodel import select

from jobagent import db, models as m
from jobagent.config import settings
from jobagent.schemas import DealBreaker, ScoreResult
from jobagent.steps import classify as C
from jobagent.steps import score as S
from jobagent.steps.export import export_run, top_missing_keywords

from profile_fixture import sample_profile


def mkjob(title, desc, location="Berlin, Germany", **kw):
    return m.Job(title=title, company=kw.pop("company", "Acme GmbH"), location=location, full_description=desc,
                 links=["https://x/1"], sources=["linkedin"], **kw)


def fp(**kw):
    base = dict(name="t", roles=[], job_types=["Internship", "Working Student", "Part-time"],
                locations=["Berlin"], work_modes=["Remote", "Hybrid", "On-site"], language_max="any")
    base.update(kw)
    return m.FilterProfile(**base)


def test_heuristic_job_types():
    h = C.heuristic_classification
    assert h("Werkstudent Data Science (m/w/d)", "Python").job_type == "Working Student"
    assert h("Machine Learning Intern", "6 months").job_type == "Internship"
    assert h("Praktikum KI-Entwicklung", "").job_type == "Internship"
    assert h("Data Analyst", "Teilzeit, 20 Stunden pro Woche").job_type == "Part-time"
    assert h("Data Scientist", "Full-time permanent position").job_type == "Full-time"
    assert h("Data Scientist", "Join our team.").job_type == "Unclear"


def test_heuristic_role_language_skills():
    c = C.heuristic_classification("Working Student GenAI", "RAG, LangGraph, FastAPI. Fluent German (C1) required. English.")
    assert c.role_category == "Gen AI" and c.german_required_level == "C1"
    assert {"RAG", "LangGraph", "FastAPI"} <= set(c.required_skills)
    c2 = C.heuristic_classification("Data Science Intern", "German is a plus. Fluent German nice to have.")
    assert c2.german_required_level is None
    assert C.heuristic_classification("Sales Intern", "Cold calling").role_category == "Other"


def test_apply_classification_rules():
    f = fp(language_max="B1")
    j = C.apply_classification(mkjob("x", ""), C.heuristic_classification("Data Scientist", "full-time"), f)
    assert j.status == "excluded" and j.exclude_reason == "Full-time"
    j = C.apply_classification(mkjob("x", ""), C.heuristic_classification("Data Scientist", "Join us"), f)
    assert j.status == "needs_review"
    j = C.apply_classification(mkjob("x", ""), C.heuristic_classification("Werkstudent Data", "fließend Deutsch"), f)
    assert j.status == "excluded" and "German C1 required" in j.exclude_reason
    j = C.apply_classification(mkjob("x", ""), C.heuristic_classification("Werkstudent Data Science", "Python"), f)
    assert j.status == "new" and j.job_type == "Working Student"
    j = C.apply_classification(mkjob("x", ""), C.heuristic_classification("Data Science Intern", "100% remote"),
                               fp(work_modes=["On-site"]))
    assert "Work mode" in j.exclude_reason


def test_verdict_rules():
    assert S.verdict(80, []) == "Apply"
    assert S.verdict(64, []) == "Apply after CV tailoring"
    assert S.verdict(39, []) == "Skip"
    assert S.verdict(90, [DealBreaker(text="German C1", hard=True)]) == "Skip"
    assert S.verdict(90, [DealBreaker(text="enrolled?", hard=False)]) == "Apply"
    settings.verdict_apply_from = 70
    assert S.verdict(66, []) == "Apply after CV tailoring"
    settings.verdict_apply_from = 65


def test_clamp_and_total():
    r = ScoreResult.model_construct(skills=55, experience=-3, title_fit=10, education=10, language=10, type_location=9,
                                    missing_wording=[], missing_skills=[], deal_breakers=[], rationale="")
    r = S.clamp(r)
    assert (r.skills, r.experience, r.type_location) == (40, 0, 5) and S.total(r) == 75


def test_heuristic_score_matches_and_flags():
    p = sample_profile()
    good = mkjob("Werkstudent Data Science", "Python, SQL, PyTorch, RAG, LangChain, Docker, Hugging Face. English.")
    r = S.score_job(good, p)
    assert r.skills >= 30 and r.type_location == 5 and not r.deal_breakers
    assert "Hugging Face -> Hugging Face" not in r.missing_wording
    german = mkjob("Working Student GenAI", "RAG, LangGraph. Fließend Deutsch (C1) required.", location="Munich, Germany")
    r2 = S.score_job(german, p)
    assert r2.language < 10 and any(d.hard for d in r2.deal_breakers)
    us = mkjob("Data Science Intern", "Python, SQL", location="Folsom, CA")
    r3 = S.score_job(us, p)
    assert any("authorisation" in d.text for d in r3.deal_breakers) and r3.type_location == 1
    syn = mkjob("ML Intern", "Experience with sklearn and GCP.")
    r4 = S.score_job(syn, p)
    assert "Scikit-learn -> sklearn" in r4.missing_wording and "Google Cloud Platform -> GCP" in r4.missing_wording


def test_store_score_and_export(tmp_path):
    p = sample_profile()
    with db.session_scope() as s:
        a = mkjob("Werkstudent Data Science", "Python, SQL, PyTorch, Kubernetes, Snowflake", first_seen_run=1,
                  posted_date=date.today(), status="new", job_type="Working Student")
        b = mkjob("Senior Data Engineer", "x", first_seen_run=1, status="excluded", exclude_reason="Full-time")
        c = mkjob("Data Scientist", "y", first_seen_run=1, status="needs_review", exclude_reason="Job type unclear")
        s.add_all([a, b, c])
        s.flush()
        aid = a.id
    with db.session_scope() as s:
        job = s.get(m.Job, aid)
    row = S.store_score(aid, S.score_job(job, p), cv_version_id=1)
    assert row.verdict in ("Apply", "Apply after CV tailoring") and row.total == S.total(S.clamp(S.score_job(job, p)))
    with db.session_scope() as s:
        assert s.get(m.Job, aid).status == "scored"
    path = export_run(1)
    assert path.name == f"Jobs_{date.today().isoformat()}.xlsx"
    wb = openpyxl.load_workbook(path)
    ws = wb["Jobs"]
    headers = [c.value for c in ws[1]]
    assert headers[:3] == ["Company Name", "Job Title", "Job Type"] and headers[-1] == "Verdict" and len(headers) == 21
    assert ws.max_row == 2 and ws.cell(2, 16).value == row.total and "Skills" in ws.cell(2, 17).value
    assert wb["Excluded"].cell(2, 5).value == "Full-time" and wb["Needs review"].max_row == 2
    # second export the same day does not overwrite
    assert export_run(1).name.endswith("_run1.xlsx")
    top = dict(top_missing_keywords())
    assert "Kubernetes" in top and "Snowflake" in top
    # CV changes -> old scores marked outdated
    assert S.mark_scores_outdated(current_version_id=2) == 1
