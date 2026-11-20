"""One PDF for e-mail applications: cover letter, CV, then your documents (newest first), under 5 MB."""
import asyncio
import os

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from jobagent import attachments as A, db, models as m
from jobagent.docs.pdf import find_soffice
from jobagent.graphs import build_graph as B

from test_10_build_graph import setup  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")


def _pdf(path, pages, size):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(*size)
    with open(path, "wb") as f:
        w.write(f)
    return path


@pytest.fixture
def built(setup):  # noqa: F811
    asyncio.run(B.start_build([setup["good"]]))
    return setup["good"]


def test_letter_then_cv_then_documents_newest_first(built, tmp_path):
    old = A.add(_pdf(tmp_path / "Internship reference.pdf", 3, (595, 842)), issued="2023-02")
    new = A.add(_pdf(tmp_path / "Transcript.pdf", 2, (500, 700)), issued="2025-09")
    A.add(_pdf(tmp_path / "Unused.pdf", 1, (300, 300)))
    out = A.application_pdf(built, attachment_ids=[old["id"], new["id"]])
    r = PdfReader(out["path"])
    assert out["pages"] == len(r.pages) == 1 + 1 + 2 + 3 and not out["over_limit"]
    assert "Dear" in r.pages[0].extract_text() or "Hiring" in r.pages[0].extract_text()       # the letter
    assert "SUMMARY" in r.pages[1].extract_text().upper()                                       # the CV
    assert [round(float(p.mediabox.width)) for p in r.pages[2:]] == [500, 500, 595, 595, 595]   # newest first
    assert out["name"].startswith("Application_") and "Nordlicht" in out["name"]
    with db.session_scope() as s:
        cv = s.exec(__import__("sqlmodel").select(m.Document).where(m.Document.job_id == built, m.Document.kind == "cv")).first()
        assert os.path.dirname(out["path"]) == os.path.dirname(cv.pdf_path)       # next to the job's CV
        cv.content = {**cv.content, "lang": "de"}
        s.add(cv)
    only_cv = A.application_pdf(built, letter=False, attachment_ids=[])
    assert only_cv["pages"] == 1 and only_cv["name"].startswith("Bewerbung_")    # a German CV: German file name
    with pytest.raises(A.AttachmentError):
        A.application_pdf(built, letter=False, cv=False, attachment_ids=[])


def test_a_big_scan_is_made_smaller(built, tmp_path):
    noise = Image.frombytes("RGB", (2600, 3500), os.urandom(2600 * 3500 * 3))
    noise.save(tmp_path / "scan.jpg", quality=97)
    assert (tmp_path / "scan.jpg").stat().st_size > A.LIMIT_MB * 1024 * 1024
    scan = A.add(tmp_path / "scan.jpg")
    out = A.application_pdf(built, attachment_ids=[scan["id"]])
    assert out["size"] <= A.LIMIT_MB * 1024 * 1024 and not out["over_limit"] and out["pages"] == 3


def test_api(built, tmp_path):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    a = A.add(_pdf(tmp_path / "Degree.pdf", 1, (595, 842)))
    with TestClient(app) as c:
        r = c.post(f"/api/apply/jobs/{built}/one-pdf", json={"letter": True, "cv": True, "attachment_ids": [a["id"]]}).json()
        assert r["pages"] == 3
        assert c.get("/api/files", params={"path": r["path"], "download": True}).status_code == 200
        assert c.post("/api/apply/jobs/999999/one-pdf", json={}).status_code == 400
