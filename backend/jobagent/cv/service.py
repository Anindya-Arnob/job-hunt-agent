"""My CV: upload, review, versions, main CV, archive, template."""
from __future__ import annotations

import difflib
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlmodel import select

from .. import db, models as m
from ..config import settings
from ..schemas import CVProfile
from .parser import parse_cv

ARCHIVE_DAYS = 30


class CVError(ValueError):
    pass


def _now():
    return datetime.now().astimezone()


# ------------------------------------------------------------------- upload ---
def upload(src: Path) -> dict:
    """Parse an uploaded file. Nothing is saved until you confirm."""
    src = Path(src)
    if src.suffix.lower() not in (".docx", ".pdf"):
        raise CVError("Please upload a .docx or .pdf file.")
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stored = settings.uploads_dir / f"{stamp}_{src.name}"
    if src.resolve() != stored.resolve():
        shutil.copy2(src, stored)
    profile = parse_cv(stored)
    return {"source_file": str(stored), "profile": profile.model_dump()}


def confirm(profile: dict, source_file: str = "", name: str = "Main CV", make_main: bool = True,
            profile_id: Optional[int] = None, note: str = "Uploaded") -> dict:
    data = CVProfile.model_validate(profile).model_dump()
    with db.session_scope() as s:
        if profile_id:
            row = s.get(m.CVProfileRow, profile_id)
            if not row:
                raise CVError("CV not found")
        else:
            row = m.CVProfileRow(name=name)
            s.add(row)
            s.flush()
        last = s.exec(select(m.CVVersion).where(m.CVVersion.profile_id == row.id)
                      .order_by(m.CVVersion.version.desc())).first()
        v = m.CVVersion(profile_id=row.id, version=(last.version + 1) if last else 1, data=data,
                        source_file=source_file, note=note)
        s.add(v)
        has_main = s.exec(select(m.CVProfileRow).where(m.CVProfileRow.is_main == True)).first()  # noqa: E712
        s.flush()
        pid, vid = row.id, v.id
    if make_main or not has_main:
        set_main(pid)
    else:
        _after_main_change()
    return {"profile_id": pid, "version_id": vid}


def save_version(profile_id: int, profile: dict, note: str = "Edited") -> dict:
    return confirm(profile, profile_id=profile_id, note=note, make_main=False)


# --------------------------------------------------------------- read/query ---
def latest_version(s, profile_id: int) -> Optional[m.CVVersion]:
    return s.exec(select(m.CVVersion).where(m.CVVersion.profile_id == profile_id)
                  .order_by(m.CVVersion.version.desc())).first()


def get_main() -> Optional[tuple[CVProfile, int]]:
    with db.session_scope() as s:
        row = s.exec(select(m.CVProfileRow).where(m.CVProfileRow.is_main == True,  # noqa: E712
                                                 m.CVProfileRow.archived_at == None)).first()  # noqa: E711
        if not row:
            return None
        v = latest_version(s, row.id)
        if not v:
            return None
        return CVProfile.model_validate(v.data), v.id


def get_profile(profile_id: int) -> Optional[tuple[CVProfile, int]]:
    with db.session_scope() as s:
        v = latest_version(s, profile_id)
        return (CVProfile.model_validate(v.data), v.id) if v else None


def get_version(version_id: int) -> Optional[CVProfile]:
    with db.session_scope() as s:
        v = s.get(m.CVVersion, version_id)
        return CVProfile.model_validate(v.data) if v else None


def list_profiles(include_archived: bool = True) -> list[dict]:
    purge_archived()
    with db.session_scope() as s:
        rows = s.exec(select(m.CVProfileRow)).all()
        out = []
        for r in rows:
            if r.archived_at and not include_archived:
                continue
            versions = s.exec(select(m.CVVersion).where(m.CVVersion.profile_id == r.id)
                              .order_by(m.CVVersion.version.desc())).all()
            out.append({
                "id": r.id, "name": r.name, "is_main": r.is_main,
                "archived_at": r.archived_at.isoformat() if r.archived_at else None,
                "delete_after": (r.archived_at + timedelta(days=ARCHIVE_DAYS)).date().isoformat() if r.archived_at else None,
                "versions": [{"id": v.id, "version": v.version, "note": v.note, "created_at": v.created_at.isoformat(),
                              "source_file": Path(v.source_file).name if v.source_file else ""} for v in versions],
            })
        return out


# ------------------------------------------------------------- main/archive ---
def _after_main_change() -> int:
    main = get_main()
    if not main:
        return 0
    from ..steps.score import mark_scores_outdated
    n = mark_scores_outdated(main[1])
    _flag_documents(main[1])
    return n


def _flag_documents(current_version_id: int) -> None:
    with db.session_scope() as s:
        for d in s.exec(select(m.Document)).all():
            outdated = d.cv_version_id is not None and d.cv_version_id != current_version_id
            content = dict(d.content or {})
            if content.get("_outdated") != outdated:
                content["_outdated"] = outdated
                d.content = content
                s.add(d)


def set_main(profile_id: int) -> int:
    with db.session_scope() as s:
        target = s.get(m.CVProfileRow, profile_id)
        if not target or target.archived_at:
            raise CVError("CV not found or archived")
        for r in s.exec(select(m.CVProfileRow)).all():
            r.is_main = r.id == profile_id
            s.add(r)
    return _after_main_change()


def rename(profile_id: int, name: str) -> None:
    with db.session_scope() as s:
        r = s.get(m.CVProfileRow, profile_id)
        r.name = name
        s.add(r)


def archive(profile_id: int) -> None:
    with db.session_scope() as s:
        r = s.get(m.CVProfileRow, profile_id)
        if not r:
            raise CVError("CV not found")
        if r.is_main:
            raise CVError("This is your main CV. Make another CV the main one (or upload a new one) before deleting it.")
        r.archived_at = _now()
        s.add(r)


def restore(profile_id: int) -> None:
    with db.session_scope() as s:
        r = s.get(m.CVProfileRow, profile_id)
        r.archived_at = None
        s.add(r)


def purge_archived(now: Optional[datetime] = None) -> int:
    now = now or _now()
    n = 0
    with db.session_scope() as s:
        for r in s.exec(select(m.CVProfileRow).where(m.CVProfileRow.archived_at != None)).all():  # noqa: E711
            if r.archived_at + timedelta(days=ARCHIVE_DAYS) <= now:
                files = []
                for v in s.exec(select(m.CVVersion).where(m.CVVersion.profile_id == r.id)).all():
                    files.append(v.source_file)
                    s.delete(v)
                s.delete(r)
                s.flush()
                for f in files:
                    _remove_upload(f, s)
                n += 1
    return n


def _remove_upload(path: str, s) -> None:
    """Delete an uploaded original if no remaining version uses it."""
    if not path:
        return
    still = s.exec(select(m.CVVersion).where(m.CVVersion.source_file == path)).first()
    p = Path(path)
    if still is None and p.exists() and str(p.resolve()).startswith(str(settings.uploads_dir.resolve())):
        try:
            p.unlink()
        except OSError:
            pass


def delete_permanently(profile_id: int) -> None:
    """Delete a CV and all its versions now (instead of waiting 30 days in the archive)."""
    with db.session_scope() as s:
        r = s.get(m.CVProfileRow, profile_id)
        if not r:
            raise CVError("CV not found")
        if r.is_main:
            raise CVError("This is your main CV. Make another CV the main one before deleting it.")
        files = []
        for v in s.exec(select(m.CVVersion).where(m.CVVersion.profile_id == r.id)).all():
            files.append(v.source_file)
            s.delete(v)
        s.delete(r)
        s.flush()
        for f in files:
            _remove_upload(f, s)


def delete_version(version_id: int) -> None:
    """Delete one old version. The newest version of a CV can't be deleted (delete the CV instead)."""
    with db.session_scope() as s:
        v = s.get(m.CVVersion, version_id)
        if not v:
            raise CVError("Version not found")
        newest = latest_version(s, v.profile_id)
        if newest and newest.id == v.id:
            raise CVError("This is the current version. Restore or save another version first, or delete the whole CV.")
        src = v.source_file
        s.delete(v)
        s.flush()
        _remove_upload(src, s)


def restore_version(version_id: int) -> dict:
    with db.session_scope() as s:
        v = s.get(m.CVVersion, version_id)
        if not v:
            raise CVError("Version not found")
        data, pid, num = v.data, v.profile_id, v.version
    return confirm(data, profile_id=pid, note=f"Restored v{num}", make_main=False)


def compare(version_a: int, version_b: int) -> list[dict]:
    a, b = get_version(version_a), get_version(version_b)
    if not a or not b:
        raise CVError("Version not found")
    out = []
    for section in ("tagline", "summary", "skills", "experience", "projects", "education", "certifications", "languages"):
        la, lb = _lines(getattr(a, section)), _lines(getattr(b, section))
        diff = [l for l in difflib.unified_diff(la, lb, lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        if diff:
            out.append({"section": section, "changes": diff})
    return out


def _lines(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    out = []
    for item in v:
        out.append(" · ".join(f"{k}: {x}" for k, x in item.model_dump().items() if x))
    return out


# ------------------------------------------------------------- honesty facts ---
NUM_RE = re.compile(r"(?<![\w.])[~+]?\d[\d,.]*(?:\.\d+)?\s?(?:%|\+|M\+?|k\+?|B)?")


def numbers_in(text: str) -> set[str]:
    out = set()
    for m_ in NUM_RE.finditer(text or ""):
        n = m_.group(0).strip().lstrip("~").rstrip(".,")
        core = re.sub(r"[^\d.]", "", n).strip(".")
        if not core:
            continue
        out.add(core)
    return out


def profile_numbers(p: CVProfile) -> set[str]:
    from ..steps.score import profile_text
    return numbers_in(profile_text(p) + "\n" + "\n".join(f"{l.language} {l.level}" for l in p.languages)
                      + "\n" + p.contact.phone)


# ----------------------------------------------------------------- template ---
DEFAULT_STYLE = {
    "navy": "1B2A4A", "teal": "2A7F8C", "text": "1A1A2E", "muted": "5A6475", "org": "323E4F",
    "tagline": "A8C8D8", "contact": "CCDDEE", "link": "7EC8E3", "font": "Calibri",
    "heading_pt": 10, "body_pt": 9, "name_pt": 20,
}


def extract_style(docx_path: Path) -> dict:
    """Read colours and fonts from a template .docx (falls back to the default style)."""
    import collections
    import zipfile
    style = dict(DEFAULT_STYLE)
    try:
        xml = zipfile.ZipFile(docx_path).read("word/document.xml").decode("utf8", "ignore")
    except Exception:
        return style
    fills = collections.Counter(re.findall(r'w:fill="([0-9A-Fa-f]{6})"', xml))
    if fills:
        style["navy"] = fills.most_common(1)[0][0].upper()
    borders = re.findall(r'<w:bottom w:color="([0-9A-Fa-f]{6})"[^>]*w:sz="(\d+)" w:val="single"', xml)
    teal = [c for c, sz in borders if c.lower() not in ("000000", "auto")]
    if teal:
        style["teal"] = collections.Counter(teal).most_common(1)[0][0].upper()
    fonts = collections.Counter(re.findall(r'w:ascii="([^"]+)"', xml))
    if fonts:
        style["font"] = fonts.most_common(1)[0][0]
    return style


def set_template(path: Path, name: str = "Uploaded template") -> dict:
    path = Path(path)
    settings.master_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.master_dir / path.name
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)
    style = extract_style(dest)
    with db.session_scope() as s:
        for t in s.exec(select(m.Template)).all():
            t.is_active = False
            s.add(t)
        s.add(m.Template(name=name, file_path=str(dest), style=style, is_active=True))
    return style


def active_style() -> dict:
    with db.session_scope() as s:
        t = s.exec(select(m.Template).where(m.Template.is_active == True)).first()  # noqa: E712
        style = dict(DEFAULT_STYLE)
        if t:
            style.update(t.style or {})
        # Your prompt fixes these sizes regardless of the template file
        style["heading_pt"], style["body_pt"] = 10, 9
        return style
