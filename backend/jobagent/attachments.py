"""My documents: your certificates, transcripts and other papers, uploaded once and used when you apply
(the Chrome helper puts them into upload fields; Apply can merge them into one PDF)."""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Optional

from sqlmodel import select

from . import db, models as m
from .config import settings

KINDS = {   # key: (label, words in an upload field's label that ask for it)
    "enrolment": ("Enrolment certificate (Immatrikulationsbescheinigung)",
                  r"enrol|immatrikulation|studienbescheinigung|student status|studierendenstatus|matriculation"),
    "transcript": ("Transcript of records (Notenspiegel)", r"transcript|notenspiegel|notenübersicht|grades|records"),
    "degree": ("Degree certificate (Abschlusszeugnis)",
               r"degree|diploma|abschluss|bachelor|master|urkunde|hochschulzeugnis"),
    "internship": ("Internship / work reference (Arbeitszeugnis)",
                   r"arbeitszeugnis|praktikumszeugnis|reference|referenz|employment|work ?cert|internship|praktikum|empfehlung"),
    "language": ("Language certificate", r"language|sprach|ielts|toefl|goethe|telc|testdaf|cambridge"),
    "other": ("Other", r""),
}
ALL_WORDS = (r"zeugnisse|certificates|further documents|additional documents|other documents|weitere|sonstige|"
             r"anlagen|attachments|supporting|additional|other")
ALLOWED = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_MB = 15


class AttachmentError(ValueError):
    pass


def folder() -> Path:
    p = Path(settings.data_dir) / "attachments"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pages(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 1
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except Exception:
        raise AttachmentError("This PDF could not be read. Is it password protected or damaged?")


def guess_kind(name: str) -> str:
    low = name.lower()
    for k, (_, words) in KINDS.items():
        if words and re.search(words, low):
            return k
    return "other"


def add(src: Path, name: str = "", kind: str = "", issued: str = "") -> dict:
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in ALLOWED:
        raise AttachmentError("Use a PDF, JPG or PNG file.")
    size = src.stat().st_size
    if size > MAX_MB * 1024 * 1024:
        raise AttachmentError(f"This file is {size // (1024 * 1024)} MB. Please use a file under {MAX_MB} MB.")
    pages = _pages(src)
    with db.session_scope() as s:
        a = m.Attachment(name=(name or src.stem).strip()[:120], kind=kind if kind in KINDS else guess_kind(name or src.stem),
                         issued=issued.strip()[:10], original_name=src.name, size=size, pages=pages)
        s.add(a)
        s.flush()
        dest = folder() / f"{a.id}{ext}"
        shutil.copyfile(src, dest)
        a.file_path = str(dest)
        s.add(a)
        s.flush()
        return as_dict(a)


def as_dict(a: m.Attachment) -> dict:
    return {"id": a.id, "name": a.name, "kind": a.kind, "kind_label": KINDS.get(a.kind, KINDS["other"])[0],
            "issued": a.issued, "original_name": a.original_name, "size": a.size, "pages": a.pages, "use": a.use,
            "is_image": Path(a.file_path).suffix.lower() != ".pdf", "created_at": a.created_at.isoformat()}


def _order(a: m.Attachment):
    """Newest first (as German recruiters expect), documents without a date last."""
    return (0 if a.issued else 1, "".join(chr(255 - ord(c)) for c in a.issued), a.id)


def all_rows(only_used: bool = False) -> list[m.Attachment]:
    with db.session_scope() as s:
        rows = list(s.exec(select(m.Attachment)).all())
        for r in rows:
            s.expunge(r)
    rows = [r for r in rows if Path(r.file_path).exists() and (r.use or not only_used)]
    return sorted(rows, key=_order)


def listing() -> list[dict]:
    return [as_dict(a) for a in all_rows()]


def update(aid: int, **changes) -> dict:
    with db.session_scope() as s:
        a = s.get(m.Attachment, aid)
        if not a:
            raise AttachmentError("Document not found")
        for k in ("name", "kind", "issued", "use"):
            if changes.get(k) is not None:
                v = changes[k]
                if k == "kind" and v not in KINDS:
                    continue
                setattr(a, k, v.strip() if isinstance(v, str) else v)
        s.add(a)
        s.flush()
        return as_dict(a)


def delete(aid: int) -> None:
    with db.session_scope() as s:
        a = s.get(m.Attachment, aid)
        if not a:
            return
        Path(a.file_path).unlink(missing_ok=True)
        s.delete(a)


def path_of(aid: int) -> Path:
    with db.session_scope() as s:
        a = s.get(m.Attachment, aid)
        if not a or not Path(a.file_path).exists():
            raise AttachmentError("Document not found")
        return Path(a.file_path)


# --------------------------------------------------------------- PDF merge ---
def _as_pdf_pages(path: Path):
    """The pages of a PDF, or a picture turned into one A4 page."""
    from pypdf import PdfReader
    if path.suffix.lower() == ".pdf":
        return list(PdfReader(str(path)).pages)
    import io
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((1654, 2339))                     # A4 at 200 dpi: sharp enough, small
    a4 = Image.new("RGB", (1654, 2339), "white")
    a4.paste(im, ((1654 - im.width) // 2, (2339 - im.height) // 2))
    buf = io.BytesIO()
    a4.save(buf, "PDF", resolution=200)
    buf.seek(0)
    return list(PdfReader(buf).pages)


def merge(paths: list[Path], dest: Path) -> Path:
    from pypdf import PdfWriter
    w = PdfWriter()
    for p in paths:
        for page in _as_pdf_pages(Path(p)):
            w.add_page(page)
    for page in w.pages:
        try:
            page.compress_content_streams()
        except Exception:
            pass
    w.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        w.write(f)
    return dest


def bundle(rows: list[m.Attachment], label: str) -> Optional[Path]:
    """One file for an upload field: the document itself when there is one, else the merged PDF (cached)."""
    rows = [r for r in rows if Path(r.file_path).exists()]
    if not rows:
        return None
    if len(rows) == 1:
        return Path(rows[0].file_path)
    key = hashlib.sha1("|".join(f"{r.id}:{Path(r.file_path).stat().st_mtime}" for r in rows).encode()).hexdigest()[:10]
    dest = folder() / "merged" / f"{label}_{key}.pdf"
    return dest if dest.exists() else merge([Path(r.file_path) for r in rows], dest)


def for_applying() -> dict[str, str]:
    """kind -> file for the upload fields of an application form ('all' = every document you marked)."""
    rows = all_rows(only_used=True)
    out = {}
    for k in KINDS:
        p = bundle([r for r in rows if r.kind == k], k)
        if p:
            out[k] = str(p)
    p = bundle(rows, "Certificates")
    if p:
        out["all"] = str(p)
    return out


def kind_for_field(text: str) -> Optional[str]:
    """Which of your documents an upload field asks for ('all' for 'Zeugnisse', 'other documents' ...)."""
    low = text.lower()
    for k, (_, words) in KINDS.items():
        if words and re.search(words, low):
            return k
    if re.search(ALL_WORDS + r"|zeugnis|certificate", low):
        return "all"
    return None
