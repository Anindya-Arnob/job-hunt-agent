"""Import your earlier Excel files (Jobs_*.xlsx, Job_Match_Summary_*.xlsx, _Report_*.xlsx),
so jobs you already handled are recognised as duplicates and never rebuilt."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from openpyxl import load_workbook
from sqlmodel import select

from . import db, models as m
from .config import settings
from .schemas import DealBreaker
from .steps.score import verdict as verdict_rule
from .textutil import dedupe_key, normalize_url

HEADER_MAP = {
    "company": ("company", "company name"),
    "title": ("job title", "title"),
    "job_type": ("job type",),
    "location": ("location",),
    "link": ("job link", "job link (url)", "url", "link"),
    "score": ("score", "match score", "match score (0–100)", "match score (0-100)"),
    "verdict": ("verdict", "verdict (sheet)"),
    "status": ("status",),
    "description": ("full job description",),
    "deal_breakers": ("deal-breakers", "hard blockers stated in letter"),
    "missing_skills": ("missing skills",),
    "missing_wording": ("missing wording",),
    "role": ("role category",),
    "keywords": ("keywords",),
    "folder": ("folder",),
    "cv_file": ("cv file",),
    "letter_file": ("cover letter file",),
}


def _find_sheet(wb):
    """The sheet and header row that hold the job table (summary files have a title block first)."""
    for ws in wb.worksheets:
        for idx, row in enumerate(ws.iter_rows(max_row=6, values_only=True), 1):
            h = [str(c).strip().lower() if c else "" for c in row]
            if any(x in h for x in ("company", "company name")) and "job title" in h:
                return ws, h, idx
    return None, None, None


def _col(head: list[str], key: str) -> Optional[int]:
    for name in HEADER_MAP[key]:
        if name in head:
            return head.index(name)
    return None


def import_file(path: Path) -> dict:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws, head, header_row = _find_sheet(wb)
    if ws is None:
        return {"file": path.name, "imported": 0, "updated": 0, "skipped": "no job table found"}
    cols = {k: _col(head, k) for k in HEADER_MAP}
    # hyperlinks are not available in read-only mode; reopen if the link column says "Open posting"
    links_by_row = {}
    if cols["link"] is not None:
        wb2 = load_workbook(path, data_only=True)
        ws2 = wb2[ws.title]
        for r_idx, row in enumerate(ws2.iter_rows(), 1):
            c = row[cols["link"]] if len(row) > cols["link"] else None
            if c is not None and c.hyperlink is not None:
                links_by_row[r_idx] = c.hyperlink.target
    imported = updated = linked = 0
    from .sync_docs import _index, attach_files
    index = _index()

    def stem(v: str) -> str:
        v = (v or "").split(" / ")[0].strip()
        for ext in (".docx", ".pdf"):
            if v.lower().endswith(ext):
                v = v[: -len(ext)]
        return v
    with db.session_scope() as s:
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
            if r_idx <= header_row:
                continue
            vals = [str(v).strip() if v is not None else "" for v in row]

            def get(k):
                i = cols.get(k)
                return vals[i] if i is not None and i < len(vals) else ""
            company, title = get("company"), get("title")
            if not company or not title:
                continue
            link = links_by_row.get(r_idx) or get("link")
            if not link.startswith("http"):
                link = ""
            url_key, dkey = normalize_url(link), dedupe_key(company, title, get("location"))
            job = None
            if url_key:
                job = s.exec(select(m.Job).where(m.Job.url_key == url_key)).first()
            if job is None:
                job = s.exec(select(m.Job).where(m.Job.dedupe_key == dkey)).first()
            built = False   # "CV built" is decided from the real files (sync_docs), not from the sheet
            if job is None:
                job = m.Job(company=company, title=title, job_type=get("job_type"), location=get("location"),
                            links=[link] if link else [], sources=["imported"], url_key=url_key, dedupe_key=dkey,
                            full_description=get("description"), role_category=get("role"),
                            keywords=[k.strip() for k in get("keywords").split(",") if k.strip()],
                            status=m.JobStatus.SCORED,
                            notes=f"Imported from {path.name}")
                s.add(job)
                s.flush()
                imported += 1
            else:
                if built and job.status in (m.JobStatus.SCORED, m.JobStatus.NEW):
                    job.status = m.JobStatus.BUILT
                if not job.full_description and get("description"):
                    job.full_description = get("description")
                s.add(job)
                updated += 1
            if index and not s.exec(select(m.Document).where(m.Document.job_id == job.id)).first():
                linked += attach_files(s, job, index, get("folder"), stem(get("cv_file")), stem(get("letter_file")))
            score = get("score")
            if score.replace(".", "").isdigit() and not s.exec(select(m.Score).where(m.Score.job_id == job.id)).first():
                total = int(float(score))
                dbs = [{"text": t.strip(), "hard": False} for t in get("deal_breakers").split(";") if t.strip()]
                s.add(m.Score(job_id=job.id, total=total, verdict=get("verdict") or verdict_rule(total, [DealBreaker(**d) for d in dbs]),
                              deal_breakers=dbs, missing_skills=[x.strip() for x in get("missing_skills").split(",") if x.strip()],
                              missing_wording=[x.strip() for x in get("missing_wording").split(";") if x.strip()],
                              rationale="Imported score", is_outdated=True))
    return {"file": path.name, "imported": imported, "updated": updated, "documents_linked": linked}


def import_previous() -> list[dict]:
    files = []
    for folder, patterns in ((settings.xl_dir, ("Jobs_*.xlsx", "Job_Match_Summary_*.xlsx")),
                             (settings.cv_out_dir, ("_Report_*.xlsx",))):
        for pat in patterns:
            files += sorted(Path(folder).glob(pat))
    out = []
    for f in files:
        if f.name.startswith("~$"):
            continue
        try:
            out.append(import_file(f))
        except Exception as e:  # a locked or odd file never stops the rest
            out.append({"file": f.name, "error": str(e)})
    from .sync_docs import sync_all
    sync_all()
    return out
