"""My documents: certificates and other papers uploaded once, then put into application forms by the helper."""
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from jobagent import attachments as A
from jobagent.apply.fields import Context, Field, decide
from jobagent.apply.profile import ApplicantProfile


def _pdf(path, pages=1):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(595, 842)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _png(path):
    Image.new("RGB", (800, 1100), (240, 240, 240)).save(path)
    return path


def test_add_list_update_delete(tmp_path):
    a = A.add(_pdf(tmp_path / "Praktikumszeugnis_Pixelforge.pdf", 2), issued="2024-09")
    b = A.add(_png(tmp_path / "IELTS.png"), issued="2025-06")
    c = A.add(_pdf(tmp_path / "Transcript of Records.pdf"))
    assert (a["kind"], b["kind"], c["kind"]) == ("internship", "language", "transcript")
    assert a["pages"] == 2 and b["is_image"] and b["pages"] == 1
    assert [x["id"] for x in A.listing()] == [b["id"], a["id"], c["id"]]       # newest first, undated last
    r = A.update(c["id"], name="Notenspiegel", kind="degree", issued="2025-10", use=False)
    assert (r["name"], r["kind"], r["issued"], r["use"]) == ("Notenspiegel", "degree", "2025-10", False)
    p = A.path_of(a["id"])
    A.delete(a["id"])
    assert not p.exists() and a["id"] not in [x["id"] for x in A.listing()]


def test_only_pdf_and_pictures(tmp_path, monkeypatch):
    (tmp_path / "x.docx").write_bytes(b"PK")
    with pytest.raises(A.AttachmentError):
        A.add(tmp_path / "x.docx")
    monkeypatch.setattr(A, "MAX_MB", 0)
    with pytest.raises(A.AttachmentError):
        A.add(_pdf(tmp_path / "big.pdf"))


@pytest.mark.parametrize("label,kind", [
    ("Transcript of records", "transcript"), ("Notenspiegel", "transcript"),
    ("Immatrikulationsbescheinigung", "enrolment"), ("Language certificate (IELTS/TOEFL)", "language"),
    ("Arbeitszeugnisse", "internship"), ("Degree certificate", "degree"),
    ("Zeugnisse", "all"), ("Weitere Unterlagen", "all"), ("Other documents", "all"), ("Certificates", "all"),
    ("Profile picture", None),
])
def test_which_document_an_upload_field_wants(label, kind):
    assert A.kind_for_field(label) == kind


def test_files_for_a_form(tmp_path):
    i1 = A.add(_pdf(tmp_path / "Internship reference A.pdf", 2), issued="2023-01")
    A.add(_pdf(tmp_path / "Internship reference B.pdf", 1), issued="2024-05")
    t = A.add(_pdf(tmp_path / "Transcript.pdf", 3))
    lang = A.add(_png(tmp_path / "Goethe B1.png"))
    A.update(lang["id"], use=False)                      # not offered when applying
    files = A.for_applying()
    assert set(files) == {"internship", "transcript", "all"}
    assert len(PdfReader(files["internship"]).pages) == 3          # both references, one file
    assert files["transcript"] == str(A.path_of(t["id"]))          # a single document goes as it is
    assert len(PdfReader(files["all"]).pages) == 6
    assert A.for_applying()["all"] == files["all"]                  # cached while nothing changed
    A.update(i1["id"], use=False)
    assert len(PdfReader(A.for_applying()["all"]).pages) == 4


def test_the_helper_fills_document_fields(tmp_path):
    t = A.add(_pdf(tmp_path / "Transcript.pdf"))
    A.add(_pdf(tmp_path / "Internship reference.pdf"))
    ctx = Context(profile=ApplicantProfile(), cv_pdf="/cv.pdf", letter_pdf="/letter.pdf", extra=A.for_applying())
    up = lambda label, i=2: decide(Field(fid="f", tag="input", type="file", label=label), ctx, file_index=i)  # noqa: E731
    assert up("Lebenslauf", 0).value == "/cv.pdf" and up("Anschreiben", 1).value == "/letter.pdf"
    assert up("Transcript of records").value == str(A.path_of(t["id"]))
    assert up("Zeugnisse").value == ctx.extra["all"]
    assert up("Language certificate").value == ctx.extra["all"]    # none of that type: all your documents
    assert up("Attachment", 1).value == "/letter.pdf"            # a 2nd plain file field stays the letter (as before)
    assert up("Further attachments", 3).value == ctx.extra["all"]
    empty = Context(profile=ApplicantProfile(), cv_pdf="/cv.pdf", letter_pdf="/letter.pdf")
    d = decide(Field(fid="f", tag="input", type="file", label="Zeugnisse"), empty, file_index=2)
    assert d.action == "leave" and "My documents" in d.why


def test_api(tmp_path):
    from fastapi.testclient import TestClient
    from jobagent.api.main import app
    with TestClient(app) as c:
        with open(_pdf(tmp_path / "a.pdf"), "rb") as f:
            r = c.post("/api/attachments", files={"file": ("a.pdf", f, "application/pdf")},
                       data={"name": "Degree", "kind": "degree", "issued": "2025-02"}).json()
        assert (r["name"], r["kind"], r["issued"]) == ("Degree", "degree", "2025-02")
        lst = c.get("/api/attachments").json()
        assert lst["items"][0]["id"] == r["id"] and any(k["value"] == "transcript" for k in lst["kinds"])
        assert c.put(f"/api/attachments/{r['id']}", json={"use": False}).json()["use"] is False
        f = c.get(f"/api/attachments/{r['id']}/file?download=true")
        assert f.status_code == 200 and f.content.startswith(b"%PDF") and "Degree.pdf" in f.headers["content-disposition"]
        assert c.post("/api/attachments", files={"file": ("x.txt", b"hi", "text/plain")}).status_code == 400
        c.delete(f"/api/attachments/{r['id']}")
        assert c.get(f"/api/attachments/{r['id']}/file").status_code == 404
