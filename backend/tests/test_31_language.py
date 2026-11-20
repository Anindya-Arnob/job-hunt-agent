"""One language setting for the CV and the cover letter: English, German, or the same as the job ad (and German
also when an English ad asks for German)."""
import asyncio
from datetime import date

import docx
import pytest

from jobagent import llm
from jobagent.config import settings
from jobagent.cv.service import numbers_in
from jobagent.docs import ats
from jobagent.docs.render import render_cv, render_letter
from jobagent.schemas import CVContent, JobForWriting, LetterContent
from jobagent.steps import write as W

from profile_fixture import sample_profile

DE_AD = ("Deine Aufgaben: Du entwickelst mit uns Modelle für die Analyse von Daten und arbeitest im Team an "
         "unserer Plattform. Dein Profil: Studium der Informatik, gute Kenntnisse in Python. Wir bieten dir flexible "
         "Arbeitszeiten und ein tolles Team.")
EN_AD = ("You will build machine learning models with our team and work on our data platform. You have experience "
         "with Python and are enrolled as a student. We offer flexible hours.")


@pytest.mark.parametrize("text,needed", [
    ("Fluent German and English are required.", True),
    ("Very good command of German and English", True),
    ("German (C1) and English", True),
    ("Sehr gute Deutsch- und Englischkenntnisse", True),
    ("German is mandatory for this role", True),
    ("Fluent English, German is a plus", False),
    ("German skills are nice to have", False),
    ("Deutschkenntnisse von Vorteil", False),
    ("You will support our German-speaking customers", False),
    ("Fluent English required", False),
])
def test_when_an_ad_needs_german(text, needed):
    assert W.german_needed(text) is needed


def test_which_language_for_which_setting(monkeypatch):
    de = JobForWriting(company="A", title="Werkstudent Data Science", description=DE_AD)
    en = JobForWriting(company="B", title="Working Student Data Science", description=EN_AD)
    en_needs = JobForWriting(company="C", title="Working Student", description=EN_AD + " Fluent German and English required.")
    en_plus = JobForWriting(company="D", title="Working Student", description=EN_AD + " German is a plus.")
    assert [W.doc_language(j, "english")[0] for j in (de, en, en_needs)] == ["en", "en", "en"]
    assert [W.doc_language(j, "german")[0] for j in (de, en, en_needs)] == ["de", "de", "de"]
    assert [W.doc_language(j, "ad")[0] for j in (de, en, en_needs, en_plus)] == ["de", "en", "de", "en"]
    assert W.doc_language(en_needs, "ad")[1] == "the job ad asks for German"
    monkeypatch.setattr(settings, "letter_language", "ad")
    assert W.doc_language(de)[0] == "de"


def test_the_writers_are_told_the_language(monkeypatch):
    seen = {}

    def fake_structured(node, schema, system, user, **kw):
        seen[node] = user
        return kw["fake"]()
    monkeypatch.setattr(llm, "structured", fake_structured)
    prof = sample_profile()
    job = JobForWriting(company="A", title="Werkstudent", description=DE_AD, lang="de")
    cv = W.write_cv(job, prof)
    assert "CV LANGUAGE: German" in seen["write_cv"] and "exactly as in the main CV" in seen["write_cv"]
    assert cv.lang == "en"            # the offline writer wrote English, so the headings stay English
    W.write_letter(job, prof, cv)
    assert "LETTER LANGUAGE: German" in seen["write_letter"] and "Mit freundlichen" in seen["write_letter"]
    W.write_cv(job.model_copy(update={"lang": "en"}), prof)
    assert "CV LANGUAGE: English" in seen["write_cv"]


def _text(path):
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


def test_german_documents_get_german_headings_and_date(tmp_path):
    from jobagent.cv.service import DEFAULT_STYLE
    prof = sample_profile()
    cv = CVContent(tagline="Data Science Werkstudent", summary="Masterstudent der Data Science.",
                   skills=prof.skills, experience=prof.experience, projects=prof.projects[:2],
                   education=prof.education, languages=prof.languages, lang="de")
    t = _text(render_cv(cv, prof, DEFAULT_STYLE, tmp_path / "cv.docx")).upper()
    assert "BERUFSERFAHRUNG" in t and "PROJEKTE" in t and "KENNTNISSE" in t and "EXPERIENCE" not in t
    en = _text(render_cv(cv.model_copy(update={"lang": "en"}), prof, DEFAULT_STYLE, tmp_path / "cv_en.docx")).upper()
    assert "EXPERIENCE" in en and "BERUFSERFAHRUNG" not in en
    L = LetterContent(subject="Bewerbung", greeting="Sehr geehrte Damen und Herren,", closing="Mit freundlichen Grüßen",
                      paragraphs=["a", "b", "c"], lang="de")
    lt = _text(render_letter(L, prof, "x", DEFAULT_STYLE, tmp_path / "l.docx", company="ACME", today=date(2026, 3, 5)))
    assert "5. März 2026" in lt and "Personalabteilung, ACME" in lt


def test_german_numbers_and_first_person():
    assert numbers_in("1,5 Jahre") == numbers_in("1.5 years") and numbers_in("10.000 Nutzer") == numbers_in("10,000 users")
    assert ats.FIRST_PERSON.search("Ich habe Modelle gebaut") and ats.FIRST_PERSON.search("in meinem Projekt")
    assert not ats.FIRST_PERSON.search("Entwicklung von Modellen für Michael")


def test_the_build_says_which_language(monkeypatch, master_cv_path):
    from test_10_build_graph import setup as _setup  # noqa: F401
    from jobagent import db, events, models as m
    from jobagent.cv import service as CV
    from jobagent.graphs import build_graph as B
    from jobagent.steps.score import score_job, store_score
    monkeypatch.setattr(settings, "letter_language", "ad")
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    prof, vid = CV.get_main()
    with db.session_scope() as s:
        j = m.Job(company="Nordlicht AI GmbH", title="Working Student ML (m/w/d)", links=["https://x/l"],
                  full_description=EN_AD + " Fluent German and English required. PyTorch, LLM, RAG.",
                  location="Berlin", job_type="Working Student", status="scored", keywords=["LLM", "RAG", "PyTorch"])
        s.add(j); s.flush(); jid = j.id
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
    store_score(jid, score_job(job, prof), vid)
    out = asyncio.run(B.start_build([jid]))
    msgs = [e["message"] for e in events.recent(out["run_id"], 500)]
    assert any("CV and cover letter in German (the job ad asks for German)" in x for x in msgs), msgs
