"""Your photo: uploaded once, cut to 3:4, and on the CV by your choice (Automatic = German CVs only)."""
import zipfile

import pytest
from PIL import Image

from jobagent.config import settings
from jobagent.cv import service as CV
from jobagent.docs import qa
from jobagent.docs.pdf import find_soffice, to_pdf
from jobagent.docs.render import render_cv
from jobagent.schemas import CVContent

from profile_fixture import sample_profile


def _img(path, size, color=(200, 150, 120)):
    Image.new("RGB", size, color).save(path)
    return path


def test_photo_is_cut_to_3_by_4(tmp_path):
    for size in [(1200, 800), (600, 1200), (500, 500)]:
        CV.set_photo(_img(tmp_path / f"p{size[0]}.png", size))
        assert Image.open(CV.photo_path()).size == CV.PHOTO_SIZE
    (tmp_path / "x.png").write_text("not a picture")
    with pytest.raises(CV.CVError):
        CV.set_photo(tmp_path / "x.png")
    CV.delete_photo()
    assert not CV.photo_path().exists()


def test_which_cv_gets_the_photo(tmp_path, monkeypatch):
    assert CV.photo_for("de") is None                     # no photo uploaded yet
    CV.set_photo(_img(tmp_path / "p.jpg", (600, 800)))
    monkeypatch.setattr(settings, "cv_photo", "auto")
    assert CV.photo_for("de") and CV.photo_for("en") is None
    monkeypatch.setattr(settings, "cv_photo", "always")
    assert CV.photo_for("de") and CV.photo_for("en")
    monkeypatch.setattr(settings, "cv_photo", "never")
    assert CV.photo_for("de") is None and CV.photo_for("en") is None


def _short_cv(lang):
    p = sample_profile()
    return p, CVContent(tagline="Data Science student", summary="Data science master's student.",
                        skills=p.skills[:3], experience=p.experience[:1], projects=p.projects[:2],
                        education=p.education, languages=p.languages, lang=lang)


def _has_image(docx_path):
    return any(n.startswith("word/media/") for n in zipfile.ZipFile(docx_path).namelist())


def test_photo_goes_in_the_header_of_a_german_cv(tmp_path, monkeypatch):
    CV.set_photo(_img(tmp_path / "p.jpg", (600, 800)))
    monkeypatch.setattr(settings, "cv_photo", "auto")
    prof, de = _short_cv("de")
    _, en = _short_cv("en")
    assert _has_image(render_cv(de, prof, CV.DEFAULT_STYLE, tmp_path / "de.docx"))
    assert not _has_image(render_cv(en, prof, CV.DEFAULT_STYLE, tmp_path / "en.docx"))
    assert not _has_image(render_cv(de, prof, CV.DEFAULT_STYLE, tmp_path / "none.docx", photo=None))


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_a_cv_with_photo_still_passes_the_layout_checks(tmp_path):
    CV.set_photo(_img(tmp_path / "p.jpg", (600, 800)))
    prof, de = _short_cv("de")
    pdf = to_pdf(render_cv(de, prof, CV.DEFAULT_STYLE, tmp_path / "cv.docx"))
    assert qa.page_count(pdf) == 1
    ok, detail = qa.band_touches_top(pdf, CV.DEFAULT_STYLE["navy"])
    assert ok, detail


def test_photo_api(tmp_path):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    with TestClient(app) as c:
        assert c.get("/api/cv/photo").status_code == 404
        assert c.get("/api/cv").json()["has_photo"] is False
        with open(_img(tmp_path / "me.png", (900, 1200)), "rb") as f:
            assert c.post("/api/cv/photo", files={"file": ("me.png", f, "image/png")}).json()["has_photo"]
        r = c.get("/api/cv/photo")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        assert c.get("/api/cv").json()["has_photo"] is True
        assert c.put("/api/settings", json={"cv_photo": "always"}).json()["cv_photo"] == "always"
        r = c.post("/api/cv/photo", files={"file": ("x.png", b"nope", "image/png")})
        assert r.status_code == 400
        c.delete("/api/cv/photo")
        assert c.get("/api/cv").json()["has_photo"] is False
