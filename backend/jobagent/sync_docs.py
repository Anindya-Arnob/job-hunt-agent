"""Keep "CV built" in line with the files that really exist in your Claude CV folder.

* Jobs with no document records (e.g. imported from your earlier Excel files) are matched
  to existing CV / cover letter files in Claude CV\\<Company>\\ and linked.
* Files you deleted or moved are noticed; the job goes back to "scored" if no CV is left.
* A job whose CV file exists is marked "CV built" automatically.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Optional

from sqlmodel import select

from . import db, models as m
from .config import settings
from .textutil import norm_title, safe_folder_name, strip_accents

_lock = threading.Lock()
DOC_EXT = (".docx", ".pdf")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(s or "").lower()).strip()


def _index() -> dict[str, list[Path]]:
    """Folder name (letters and digits only, lower-case) -> files, one level below Claude CV.
    Compact keys make "OKAPIOrbits GmbH" match the company "OKAPI:Orbits GmbH"."""
    out: dict[str, list[Path]] = {}
    root = settings.cv_out_dir
    if not root.exists():
        return out
    for d in root.iterdir():
        if d.is_dir() and not d.name.startswith("_"):
            try:
                out.setdefault(_compact(d.name), []).extend(
                    f for f in d.iterdir() if f.suffix.lower() in DOC_EXT and not f.name.startswith("~$"))
            except OSError:
                pass
    return out


def _pairs(files: list[Path], marker: str) -> dict[str, dict[str, Path]]:
    """stem -> {'.docx': path, '.pdf': path} for files whose name contains marker."""
    out: dict[str, dict[str, Path]] = {}
    for f in files:
        if marker.lower() in f.name.lower():
            out.setdefault(f.stem, {})[f.suffix.lower()] = f
    return out


_STOP = {"m", "w", "d", "f", "x", "h", "and", "the", "for", "of", "in", "im", "der", "die", "das", "und", "a", "an",
         "to", "at", "with", "all", "genders", "gn", "div"}


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if w not in _STOP and len(w) > 1}


def _stem_words(stem: str, job: m.Job) -> set[str]:
    from .config import owner_name
    owner = _words(owner_name())
    company = _words(job.company)
    return _words(stem) - owner - company - {"cv", "cover", "letter"}


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", strip_accents(text or "").lower())


_TYPES = {"intern": {"intern", "internship", "praktikum", "praktikant", "stage", "stagiaire", "pflichtpraktikum"},
          "student": {"werkstudent", "werkstudentin", "student"},
          "part": {"teilzeit", "parttime"}}


def _kind(words: set[str]) -> set[str]:
    return {k for k, vocab in _TYPES.items() if words & vocab}


def _similarity(job: m.Job, stem: str) -> float:
    a, b = _words(job.title), _stem_words(stem, job)
    if not a or not b:
        return 0.0
    ka, kb = _kind(a), _kind(b)
    if ka and kb and not (ka & kb):
        return 0.0          # an internship CV is never another job's working-student CV
    # "AI/ML Development Intern" vs file "..._AIML_Development_Intern": same once punctuation is gone
    ct, cs = _compact(" ".join(sorted(a, key=job.title.lower().find))), _compact(stem)
    if len(ct) >= 8 and ct in cs:
        return 1.0
    return len(a & b) / len(a | b)


def _assign(jobs: list[m.Job], pairs: dict[str, dict[str, Path]], hints: dict[int, str]) -> dict[int, dict[str, Path]]:
    """Give each file to the job whose title fits it best; one file per job (true duplicates may share)."""
    out: dict[int, dict[str, Path]] = {}
    used: dict[str, int] = {}
    for j in jobs:                                # exact names from your earlier report first
        st = hints.get(j.id)
        if st and st in pairs:
            out[j.id] = pairs[st]
            used[st] = j.id
    cands = []
    for j in jobs:
        if j.id in out:
            continue
        for stem in pairs:
            sim = _similarity(j, stem)
            if sim >= 0.5:
                cands.append((sim, j, stem))
    cands.sort(key=lambda t: -t[0])
    by_id = {j.id: j for j in jobs}
    for sim, j, stem in cands:
        if j.id in out:
            continue
        owner = used.get(stem)
        if owner is not None and norm_title(by_id[owner].title) != norm_title(j.title):
            continue                              # file already belongs to a different job
        out[j.id] = pairs[stem]
        used.setdefault(stem, j.id)
    # one job and one file left over in this folder: they belong together if they share some words
    left_jobs = [j for j in jobs if j.id not in out]
    left_files = [st for st in pairs if st not in used]
    if len(left_jobs) == 1 and len(left_files) == 1 and _similarity(left_jobs[0], left_files[0]) >= 0.2:
        out[left_jobs[0].id] = pairs[left_files[0]]
    return out


def _folders_for(job: m.Job, index: dict[str, list[Path]], hint_folder: str = "") -> list[Path]:
    out, seen = [], set()
    for n in (hint_folder, job.company or ""):
        k = _compact(n)
        if k and k in index and k not in seen:
            seen.add(k)
            out += index[k]
    return out


def attach_folder(s, jobs: list[m.Job], files: list[Path], cv_hints: dict[int, str] | None = None,
                  letter_hints: dict[int, str] | None = None) -> int:
    """Link existing CV / letter files in one company folder to that company's jobs."""
    made = 0
    for kind, marker, hints in (("cv", "_CV_", cv_hints or {}), ("letter", "_Cover_Letter_", letter_hints or {})):
        for jid, got in _assign(jobs, _pairs(files, marker), hints).items():
            s.add(m.Document(job_id=jid, kind=kind, docx_path=str(got.get(".docx", "")),
                             pdf_path=str(got.get(".pdf", "")), content={"_imported": True}, qa_results=[],
                             qa_passed=False))
            made += 1
    return made


def attach_files(s, job: m.Job, index: dict[str, list[Path]], hint_folder: str = "",
                 cv_stem: str = "", letter_stem: str = "") -> int:
    """Link files for a single job (used by the importer when the report names the exact files)."""
    files = _folders_for(job, index, hint_folder)
    if not files:
        return 0
    return attach_folder(s, [job], files, {job.id: cv_stem} if cv_stem else {}, {job.id: letter_stem} if letter_stem else {})


def _retire_duplicates(index: dict[str, list[Path]], jobs: list[m.Job], docs_by_job: dict) -> int:
    """One CV + one letter per job: an extra file that clearly is an older version of a job that already
    has its documents goes to Claude CV/_old_versions/ (moved, never deleted)."""
    from .graphs.build_graph import retire_file
    linked = {p for ds in docs_by_job.values() for d in ds for p in (d.docx_path, d.pdf_path) if p}
    by_company: dict[str, list[m.Job]] = {}
    for j in jobs:
        if docs_by_job.get(j.id):
            by_company.setdefault(_compact(j.company or ""), []).append(j)
    n = 0
    for folder, files in index.items():
        cands = by_company.get(folder)
        if not cands:
            continue
        for f in files:
            if str(f) in linked or not f.exists():
                continue
            kind = "_CV_" if "_CV_" in f.name else "_Cover_Letter_" if "_Cover_Letter_" in f.name else ""
            if not kind:
                continue
            sims = sorted(((_similarity(j, f.stem), j) for j in cands), key=lambda t: -t[0])
            if sims and sims[0][0] >= 0.6 and (len(sims) == 1 or sims[0][0] - sims[1][0] >= 0.15):
                if retire_file(f):
                    n += 1
    return n


def sync_all() -> dict:
    """Check every job against the files on disk and update its built state."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        index = _index()
        counts = {"linked": 0, "missing_files": 0, "marked_built": 0, "marked_not_built": 0, "retired": 0}
        with db.session_scope() as s:
            jobs = s.exec(select(m.Job).where(m.Job.status != m.JobStatus.EXCLUDED)).all()
            docs_by_job: dict[int, list[m.Document]] = {}
            for d in s.exec(select(m.Document)).all():
                docs_by_job.setdefault(d.job_id, []).append(d)
            unlinked: dict[str, list[m.Job]] = {}
            for job in jobs:
                docs = docs_by_job.get(job.id, [])
                alive = []
                for d in docs:
                    dx = d.docx_path if d.docx_path and Path(d.docx_path).exists() else ""
                    pf = d.pdf_path if d.pdf_path and Path(d.pdf_path).exists() else ""
                    if (dx, pf) != (d.docx_path, d.pdf_path):
                        counts["missing_files"] += 1
                        if not dx and not pf:
                            s.delete(d)
                            continue
                        d.docx_path, d.pdf_path = dx, pf
                        s.add(d)
                    alive.append(d)
                docs_by_job[job.id] = alive
                if not alive and index:
                    unlinked.setdefault(_compact(job.company or ""), []).append(job)
            linked_paths = {p for ds in docs_by_job.values() for d in ds for p in (d.docx_path, d.pdf_path) if p}
            for folder, fjobs in unlinked.items():
                files = [f for f in index.get(folder, []) if str(f) not in linked_paths]
                if files:
                    counts["linked"] += attach_folder(s, fjobs, files)
            s.flush()
            docs_by_job = {}
            for d in s.exec(select(m.Document)).all():
                docs_by_job.setdefault(d.job_id, []).append(d)
            counts["retired"] = _retire_duplicates(index, jobs, docs_by_job)
            for job in jobs:
                alive = docs_by_job.get(job.id, [])
                has_cv = any(d.kind == "cv" for d in alive)
                if has_cv and job.status in (m.JobStatus.NEW, m.JobStatus.SCORED, m.JobStatus.NEEDS_REVIEW):
                    job.status = m.JobStatus.BUILT
                    counts["marked_built"] += 1
                    s.add(job)
                elif not has_cv and job.status == m.JobStatus.BUILT:
                    job.status = m.JobStatus.SCORED
                    counts["marked_not_built"] += 1
                    s.add(job)
        return counts
    finally:
        _lock.release()


def doc_state(job_id: int, docs: list[m.Document]) -> dict:
    out = {"cv": None, "letter": None}
    for d in docs:
        if d.job_id != job_id:
            continue
        built_at = None
        for f in (d.pdf_path, d.docx_path):      # date of the file itself (also right for earlier builds)
            try:
                if f:
                    from datetime import datetime
                    built_at = datetime.fromtimestamp(Path(f).stat().st_mtime).astimezone().isoformat()
                    break
            except OSError:
                pass
        out[d.kind] = {"id": d.id, "pdf_path": d.pdf_path, "docx_path": d.docx_path, "qa_passed": d.qa_passed,
                       "imported": bool((d.content or {}).get("_imported")), "built_at": built_at}
    return out
