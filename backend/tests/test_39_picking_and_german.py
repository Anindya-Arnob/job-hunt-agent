"""Projects are picked with the same words as the ATS score; ways of working go into bullets, not the skills list;
a German CV has no English leftovers in its fixed words."""
import docx

from jobagent.cv import projects as P
from jobagent.cv import service as CV
from jobagent.docs.render import de_words, render_cv
from jobagent.graphs import build_graph as B
from jobagent.schemas import CVContent, JobForWriting, ProjectItem
from jobagent.steps import write as W
from jobagent.vocab import is_practice

from profile_fixture import sample_profile

AD = ("Erfahrung mit automatisierten Tests, Fehlerbehandlung, Monitoring und Kostenkontrolle produktiver "
      "KI-Anwendungen. Python.")


def test_project_picking_counts_the_words_of_the_ad():
    job = JobForWriting(company="BITS", title="AI Engineer", description=AD)
    terms = P.weighted_terms(job)
    for t in ("Automated Testing", "Error Handling", "Monitoring", "Cost Control"):
        assert t in terms
    prod = ProjectItem(name="Agent app", bullets=["Added retries and fallbacks, 200 unit tests and live progress monitoring."])
    plain = ProjectItem(name="Notebook", bullets=["Trained a model in Python."])
    assert P.relevance(prod, terms)[0] > P.relevance(plain, terms)[0]


def test_practices_are_not_skills():
    for t in ("Cost Control", "Error Handling", "Monitoring", "Performance", "Integrations", "Fehlerbehandlung"):
        assert is_practice(t), t
    for t in ("pytest", "Automated Testing", "LangGraph", "Docker", "Tool Calling"):
        assert not is_practice(t), t


def test_ways_of_working_leave_the_skills_list_when_a_bullet_shows_them(monkeypatch):
    monkeypatch.setattr(B.qa, "unknown_tools", lambda c, p: [])      # this test is about the practices only
    prof = sample_profile()
    cv = CVContent(tagline="t", summary="s",
                   skills=[{"category": "Tools", "items": ["pytest", "Cost Control", "Performance"]}],
                   experience=[], projects=[{"name": "Agent app", "bullets": ["Cut costs with a daily budget cap and token tracking."]}],
                   education=[{"degree": "M.Sc."}])
    done = B._repair(cv, prof, JobForWriting(company="x", title="AI Engineer", description=AD), {"matched": []})
    assert cv.skills[0].items == ["pytest", "Performance"]          # Performance: no bullet shows it, so it stays
    assert any("Cost Control" in d for d in done)


def test_a_ticked_way_of_working_becomes_a_bullet(master_cv_path):
    from jobagent.steps import boost
    draft = CV.upload(master_cv_path)
    CV.confirm(draft["profile"], draft["source_file"])
    before, _ = CV.get_main()
    proj = before.projects[0].name
    boost.add_to_main([{"term": "Monitoring", "where": f"{proj}: live progress tracking of every run"},
                       {"term": "Cost Control", "where": ""}])
    after, _ = CV.get_main()
    skills = [i for g in after.skills for i in g.items]
    assert "Monitoring" not in skills and "Cost Control" not in skills
    bullets = [b for p in after.projects for b in p.bullets]
    assert any("live progress tracking" in b for b in bullets) and any("cost control" in b.lower() for b in bullets)


def _text(path):
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


def test_a_german_cv_has_no_english_fixed_words(tmp_path):
    assert de_words("Internship · Berlin, Germany") == "Praktikum · Berlin, Deutschland"
    assert de_words("MAKAUT University, India") == "MAKAUT University, Indien"
    prof = sample_profile()
    prof.contact.location = "Berlin, Germany"
    exp = prof.experience[0].model_copy(update={"meta": "Internship · Berlin, Germany", "dates": "Dec 2025 – Present"})
    c = CVContent(tagline="t", summary="Masterstudent.", skills=prof.skills[:2], experience=[exp], projects=prof.projects[:1],
                  education=prof.education, lang="de")
    t = _text(render_cv(c, prof, CV.DEFAULT_STYLE, tmp_path / "de.docx", photo=None))
    assert "Praktikum · Berlin, Deutschland" in t and "Germany" not in t and "heute" in t
    en = _text(render_cv(c.model_copy(update={"lang": "en"}), prof, CV.DEFAULT_STYLE, tmp_path / "en.docx", photo=None))
    assert "Internship · Berlin, Germany" in en
    assert "German verb" in W.cv_language_line("de")
