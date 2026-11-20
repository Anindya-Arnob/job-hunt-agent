"""10 CV templates: each one renders a one-page, ATS-readable CV and letter, follows the language and the photo
setting, and a whole build passes its checks in it."""
import asyncio

import pytest
from pypdf import PdfReader

from jobagent.config import settings
from jobagent.cv import service as CV
from jobagent.docs import qa, templates as T
from jobagent.docs.pdf import find_soffice, to_pdf
from jobagent.docs.render import render_cv, render_letter
from jobagent.graphs import build_graph as B
from jobagent.schemas import CVContent, LetterContent

from profile_fixture import sample_profile
from test_10_build_graph import setup  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
IDS = [t["id"] for t in T.TEMPLATES]


def test_ten_distinct_templates():
    assert len(IDS) == 10 == len(set(IDS))
    looks = {(t.get("header"), t.get("heading"), t.get("dates"), t.get("font"), t.get("navy"), t.get("teal"))
             for t in T.TEMPLATES}
    assert len(looks) == 10
    assert T.get("nope")["id"] == "classic"
    st = T.style_for("europass", dict(CV.DEFAULT_STYLE))
    assert st["navy"] == "003399" and st["heading"] == "label" and (st["heading_pt"], st["body_pt"]) == (10, 9)
    own = dict(CV.DEFAULT_STYLE, navy="123456")
    assert T.style_for("classic", own)["navy"] == "123456"            # Classic keeps your uploaded colours


def _cv(lang="en"):
    p = sample_profile()
    return p, CVContent(tagline="Data Science student", summary=p.summary, skills=p.skills[:5],
                        experience=p.experience, projects=p.projects[:3], education=p.education,
                        certifications=p.certifications[:1], languages=p.languages, lang=lang)


@pytest.mark.parametrize("tid", IDS)
def test_each_template_is_one_readable_page(tid, tmp_path):
    prof, c = _cv("de")
    from PIL import Image
    Image.new("RGB", (600, 800), (180, 150, 130)).save(tmp_path / "me.jpg")
    CV.set_photo(tmp_path / "me.jpg")
    style = T.style_for(tid, dict(CV.DEFAULT_STYLE))
    docx = render_cv(c, prof, style, tmp_path / "cv.docx", photo=CV.photo_path())
    pdf = to_pdf(docx)
    assert qa.page_count(pdf) == 1
    text = " ".join(PdfReader(str(pdf)).pages[0].extract_text().split())
    for must in (prof.name.split()[0], "BERUFSERFAHRUNG", "PROJEKTE", c.experience[0].title.split()[0], "Python"):
        assert must.lower() in text.lower(), (tid, must)
    checks = qa.check_cv(c, prof, docx, pdf, style, projects_wanted=3)
    bad = [ch for ch in checks if not ch.passed and not ch.soft]
    assert not bad, (tid, [(ch.name, ch.detail) for ch in bad])
    has_band_check = any(ch.name == "Header band touches top edge" for ch in checks)
    assert has_band_check == (style["header"] == "band")
    has_img = any(n.startswith("word/media/") for n in __import__("zipfile").ZipFile(docx).namelist())
    assert has_img == T.get(tid)["photo"]                  # no photo area: no photo, even when you have one
    L = LetterContent(subject="Bewerbung", greeting="Sehr geehrte Damen und Herren,", paragraphs=["Absatz eins.", "Zwei.", "Drei."],
                      closing="Mit freundlichen Grüßen", lang="de")
    lpdf = to_pdf(render_letter(L, prof, c.tagline, style, tmp_path / "letter.docx", company="ACME"))
    assert qa.page_count(lpdf) == 1 and all(ch.passed or ch.soft for ch in qa.check_letter(L, prof, lpdf, style))


@pytest.mark.parametrize("tid", ["lebenslauf", "sidebar", "europass"])
def test_a_whole_build_passes_in_the_template(tid, setup, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "cv_template", tid)
    out = asyncio.run(B.start_build([setup["good"]]))
    r = out["results"][0]
    assert r["status"] == "built", (tid, r.get("failed_checks"))


def test_api_lists_and_previews(tmp_path, master_cv_path):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    with TestClient(app) as c:
        lst = c.get("/api/cv/templates").json()
        assert len(lst["templates"]) == 10 and lst["active"] == "classic"
        assert c.get("/api/cv/templates/swiss/preview.png").status_code == 404     # no main CV yet
        draft = CV.upload(master_cv_path)
        CV.confirm(draft["profile"], draft["source_file"])
        r = c.get("/api/cv/templates/swiss/preview.png")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
        assert c.get("/api/cv/templates/nope/preview.png").status_code == 404
        assert c.put("/api/settings", json={"cv_template": "modern"}).json()["cv_template"] == "modern"
        assert c.get("/api/cv/templates").json()["active"] == "modern"
        assert CV.active_style()["template"] == "modern"
