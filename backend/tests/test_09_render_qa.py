from pathlib import Path

import pytest
from docx.shared import Twips

from jobagent.cv.parser import parse_docx
from jobagent.cv.service import DEFAULT_STYLE
from jobagent.docs import qa
from jobagent.docs.pdf import find_soffice, to_pdf
from jobagent.docs.render import Builder, render_cv, render_letter
from jobagent.schemas import JobForWriting
from jobagent.steps.write import heuristic_cv, heuristic_letter, limit_em_dashes

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")

JOB = JobForWriting(company="Nordlicht AI GmbH", title="Working Student Machine Learning (NLP/LLM)",
                    job_type="Working Student", location="Berlin",
                    description="Fine-tune LLMs with Hugging Face Transformers, LoRA, PEFT. RAG with LangChain. "
                                "Python, PyTorch, Docker, SQL, sklearn, GCP. Kubernetes is a plus.",
                    keywords=["LLM", "RAG", "LangChain", "PyTorch"], missing_wording=["Scikit-learn -> sklearn"],
                    missing_skills=["Kubernetes"], deal_breakers=["German C1 required (CV: A2) (hard)"])
STYLE = dict(DEFAULT_STYLE)


@pytest.fixture(scope="module")
def prof():
    return parse_docx(Path(__file__).parent / "fixtures" / "master_cv.docx")


def fit(prof, tmp_path, job=JOB):
    for level in range(5):
        cv = heuristic_cv(job, prof, level)
        d = render_cv(cv, prof, STYLE, tmp_path / f"cv{level}.docx")
        pdf = to_pdf(d)
        checks = qa.check_cv(cv, prof, d, pdf, STYLE)
        if qa.passed(checks):
            return cv, d, pdf, checks, level
    raise AssertionError([c.as_dict() for c in checks if not c.passed])


def test_real_cv_fits_one_page_and_passes_all_checks(prof, tmp_path):
    cv, d, pdf, checks, level = fit(prof, tmp_path)
    assert qa.page_count(pdf) == 1 and len(cv.projects) >= 3
    names = {c.name for c in checks}
    assert {"Exactly 1 page", "Header band touches top edge", "Only tools from your main CV",
            "Only numbers from your main CV", "At most one em dash", "Headings 10 pt, body 9 pt"} <= names
    # wording swapped to the posting's term where the skill exists
    text = " ".join(b for p in cv.projects for b in p.bullets) + cv.summary
    assert "Kubernetes" not in text and "Kubernetes" not in str(cv.skills)


def test_qa_catches_invented_tool_number_and_dashes(prof, tmp_path):
    cv = heuristic_cv(JOB, prof, 3)
    cv.skills[0].items.append("Kubernetes")
    cv.projects[0].bullets[0] = "Cut latency by 37% — then — again — with caching."
    d = render_cv(cv, prof, STYLE, tmp_path / "bad.docx")
    checks = {c.name: c for c in qa.check_cv(cv, prof, d, None, STYLE)}
    assert not checks["Only tools from your main CV"].passed and "Kubernetes" in checks["Only tools from your main CV"].detail
    assert not checks["Only numbers from your main CV"].passed and "37" in checks["Only numbers from your main CV"].detail
    assert not checks["At most one em dash"].passed
    fixed = limit_em_dashes(cv)
    assert qa.all_text_cv(fixed).count("—") <= 1
    assert any("Kubernetes" in i for i in qa.issues(list(checks.values())))


def test_qa_detects_two_pages(prof, tmp_path):
    cv = heuristic_cv(JOB, prof, 0)
    cv.projects = [p.model_copy() for p in prof.projects]   # everything -> too long
    d = render_cv(cv, prof, STYLE, tmp_path / "long.docx")
    pdf = to_pdf(d)
    c = {x.name: x for x in qa.check_cv(cv, prof, d, pdf, STYLE)}
    assert not c["Exactly 1 page"].passed and "lines too long" in c["Exactly 1 page"].detail


def test_qa_detects_gap_above_band(prof, tmp_path, monkeypatch):
    import jobagent.docs.render as R
    orig = R.Builder.__init__

    def with_gap(self, style, compact=0):
        orig(self, style, compact)
        self.doc.sections[0].top_margin = Twips(700)
    monkeypatch.setattr(R.Builder, "__init__", with_gap)
    cv = heuristic_cv(JOB, prof, 4)
    d = render_cv(cv, prof, STYLE, tmp_path / "gap.docx")
    ok, detail = qa.band_touches_top(to_pdf(d), STYLE["navy"])
    assert not ok and "white gap" in detail


def test_letter_one_page_honest_blockers(prof, tmp_path):
    cv = heuristic_cv(JOB, prof, 2)
    L = heuristic_letter(JOB, prof, cv)
    text = " ".join(L.paragraphs)
    assert "German C1" in text and "A2" in text and "Kubernetes" in text      # gaps named plainly
    assert "up to 20 hours a week" in text
    d = render_letter(L, prof, cv.tagline, STYLE, tmp_path / "letter.docx", company=JOB.company)
    pdf = to_pdf(d)
    assert qa.passed(qa.check_letter(L, prof, pdf, STYLE))
    intern = JOB.model_copy(update={"job_type": "Internship", "deal_breakers": [], "missing_skills": []})
    L2 = heuristic_letter(intern, prof, cv)
    assert "start date and duration" in " ".join(L2.paragraphs) and not L2.blockers_stated


def test_qa_detects_band_not_reaching_right_edge(prof, tmp_path, monkeypatch):
    """The Word bug: band stopped short of the right page edge."""
    import jobagent.docs.render as R
    orig = R._tbl_props

    def narrow(table, width, cols, indent=0):
        if indent < 0:   # the header band
            return orig(table, width - 900, [cols[0] - 900], indent)
        return orig(table, width, cols, indent)
    monkeypatch.setattr(R, "_tbl_props", narrow)
    cv = heuristic_cv(JOB, prof, 4)
    d = render_cv(cv, prof, STYLE, tmp_path / "narrow.docx")
    ok, detail = qa.band_touches_top(to_pdf(d), STYLE["navy"])
    assert not ok and "right" in detail
