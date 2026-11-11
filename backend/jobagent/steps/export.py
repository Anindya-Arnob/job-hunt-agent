"""Excel exports from the database, in the same columns as your current Jobs_<date>.xlsx."""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlmodel import select

from .. import db, models as m
from ..config import settings
from .score import breakdown_text

JOB_COLUMNS = [
    ("Company Name", 26), ("Job Title", 40), ("Job Type", 16), ("Role Category", 22), ("Location", 26),
    ("Remote / Hybrid / On-site", 14), ("Posted Date", 12), ("Source", 18), ("Job Link (URL)", 40),
    ("Full Job Description", 80), ("Required Skills", 40), ("Keywords", 40), ("Language Requirements", 24),
    ("Salary", 16), ("Application Deadline", 16),
    ("Match Score (0–100)", 10), ("Score Breakdown", 44), ("Missing Wording", 40), ("Missing Skills", 40),
    ("Deal-Breakers", 40), ("Verdict", 22),
]
NAVY = "1B2A4A"
SOURCE_LABEL = {"linkedin": "LinkedIn", "glassdoor": "Glassdoor", "indeed": "Indeed", "stepstone": "StepStone",
                "xing": "XING", "wellfound": "Wellfound", "ats_feeds": "Company site", "arbeitsagentur": "Arbeitsagentur",
                "arbeitnow": "Arbeitnow", "remoteok": "RemoteOK", "career_pages": "Company site",
                "imported": "Earlier Excel"}


def _src(source: str, link: str) -> str:
    """'web' -> the site it came from, e.g. 'arbeitnow.com (web search)'."""
    if source != "web":
        return SOURCE_LABEL.get(source, source)
    from urllib.parse import urlparse
    host = urlparse(link or "").netloc.lower().removeprefix("www.")
    return f"{host} (web search)" if host else "Web search"


def _header(ws, cols):
    for i, (name, width) in enumerate(cols, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"


def job_row(job: m.Job, sc: Optional[m.Score]) -> list:
    db_text = "; ".join(d.get("text", "") + (" (hard)" if d.get("hard") else "") for d in (sc.deal_breakers if sc else []))
    return [
        job.company, job.title, job.job_type, job.role_category, job.location, job.work_mode,
        job.posted_date, ", ".join(_src(s, job.link) for s in job.sources), job.link,
        (job.full_description or job.notes)[:32000], ", ".join(job.required_skills), ", ".join(job.keywords),
        job.language_req, job.salary, job.deadline,
        sc.total if sc else None, breakdown_text(sc) if sc else "", "; ".join(sc.missing_wording) if sc else "",
        ", ".join(sc.missing_skills) if sc else "", db_text, sc.verdict if sc else "",
    ]


def _jobs_with_scores(where) -> list[tuple[m.Job, Optional[m.Score]]]:
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job).where(where)).all() if where is not None else s.exec(select(m.Job)).all()
        scores = {sc.job_id: sc for sc in s.exec(select(m.Score)).all()}
    rows = [(j, scores.get(j.id)) for j in jobs]
    rows.sort(key=lambda t: -(t[1].total if t[1] else -1))
    return rows


def export_jobs(path: Path, rows: Iterable[tuple[m.Job, Optional[m.Score]]], excluded=None, needs_review=None) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    _header(ws, JOB_COLUMNS)
    for r, (job, sc) in enumerate(rows, 2):
        for c, v in enumerate(job_row(job, sc), 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.alignment = Alignment(wrap_text=c not in (10,), vertical="top")
        if job.link:
            ws.cell(row=r, column=9).hyperlink = job.link
            ws.cell(row=r, column=9).font = Font(color="0563C1", underline="single")
        ws.cell(row=r, column=7).number_format = "yyyy-mm-dd"
        ws.row_dimensions[r].height = 60
    for title, items in (("Needs review", needs_review), ("Excluded", excluded)):
        if items is None:
            continue
        ws2 = wb.create_sheet(title)
        cols = [("Company", 26), ("Job Title", 40), ("Location", 26), ("Source", 16), ("Reason", 50), ("Job Link", 50)]
        _header(ws2, cols)
        for r, job in enumerate(items, 2):
            vals = [job.company, job.title, job.location, ", ".join(job.sources), job.exclude_reason, job.link]
            for c, v in enumerate(vals, 1):
                ws2.cell(row=r, column=c, value=v)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def export_run(run_id: int, day: Optional[date] = None) -> Path:
    """Jobs_YYYY-MM-DD.xlsx with every scored or needs-review job first seen in this run."""
    day = day or date.today()
    path = settings.xl_dir / f"Jobs_{day.isoformat()}.xlsx"
    rows = _jobs_with_scores(m.Job.first_seen_run == run_id)
    main = [(j, s) for j, s in rows if j.status not in (m.JobStatus.EXCLUDED, m.JobStatus.NEEDS_REVIEW)]
    review = [j for j, _ in rows if j.status == m.JobStatus.NEEDS_REVIEW]
    excl = [j for j, _ in rows if j.status == m.JobStatus.EXCLUDED]
    if path.exists():
        path = path.with_name(f"Jobs_{day.isoformat()}_run{run_id}.xlsx")
    return export_jobs(path, main, excluded=excl, needs_review=review)


def export_all(path: Path) -> Path:
    rows = _jobs_with_scores(m.Job.status != m.JobStatus.EXCLUDED)
    return export_jobs(path, rows)


def top_missing_keywords(n: int = 15) -> list[tuple[str, int]]:
    with db.session_scope() as s:
        scores = s.exec(select(m.Score)).all()
    c = Counter()
    for sc in scores:
        for k in set(x.strip() for x in sc.missing_skills if x.strip()):
            c[k] += 1
    return c.most_common(n)
