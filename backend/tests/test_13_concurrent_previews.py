"""Regression: parallel page-image requests crashed the app (pdfium is not thread-safe)."""
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from jobagent.docs.pdf import find_soffice, to_pdf

pytestmark = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")


def test_many_parallel_page_images(tmp_path, master_cv_path):
    from jobagent.api.main import app
    from jobagent.config import settings
    import shutil
    src = tmp_path / "Job"
    src.mkdir(exist_ok=True)
    doc = src / "cv.docx"
    shutil.copy(master_cv_path, doc)
    pdf = to_pdf(doc)
    with TestClient(app) as c:
        pages = c.get("/api/files/pages", params={"path": str(pdf)}).json()["pages"]
        assert pages >= 2

        def get(i):
            r = c.get("/api/files/page-image", params={"path": str(pdf), "page": i % pages, "t": i})
            return r.status_code, r.headers.get("content-type"), len(r.content)
        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(get, range(60)))
        assert all(code == 200 and ct == "image/png" and n > 10000 for code, ct, n in res), res[:3]
        assert c.get("/api/files/page-image", params={"path": str(pdf), "page": 9}).status_code == 404
    _ = settings, subprocess
