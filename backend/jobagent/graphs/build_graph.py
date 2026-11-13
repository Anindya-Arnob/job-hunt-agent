"""The build graph (LangGraph).

review (pause for you) -> next_job -> write_cv -> check_cv -> render_qa --fail--> write_cv (max 3)
                                                              \\--pass--> write_letter -> letter_qa -> save -> next_job
Finally the _Report_<date>.xlsx is written.
"""
from __future__ import annotations

import logging
import operator
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlmodel import select

from .. import db, events, llm, models as m
from ..config import settings
from ..cv import service as cvs
from ..docs import qa
from ..docs.pdf import PdfUnavailable, to_pdf
from ..docs.render import render_cv, render_letter
from ..schemas import CVContent, CVProfile, JobForWriting, LetterContent
from ..steps import write as W
from ..textutil import safe_filename_part, safe_folder_name

log = logging.getLogger(__name__)


class BuildState(TypedDict, total=False):
    run_id: int
    job_ids: list
    queue: list
    cv_profile_id: Optional[int]
    rebuild: bool
    current: Optional[int]
    cv: Optional[dict]
    letter: Optional[dict]
    issues: list
    attempts: int
    level: int
    qa_cv: list
    qa_letter: list
    letter_attempts: int
    tmp_dir: str
    results: Annotated[list, operator.add]
    report_path: str
    wprof: Optional[dict]          # your CV with only the picked projects (what the writer sees)
    tprof: Optional[dict]          # your CV + facts of the picked GitHub projects (what the checks accept)
    picked: list
    proj_info: dict
    fact: str
    ats_before: dict
    ats_after: dict
    review: Optional[dict]
    reviewed: bool


# ---------------------------------------------------------------- helpers ---
def role_for_filename(title: str) -> str:
    t = re.sub(r"\((m|w|d|f|x|div|all genders?)[/|,\s]*(m|w|d|f|x)?[/|,\s]*(m|w|d|f|x)?\)", "", title, flags=re.I)
    t = re.sub(r"\b(m/w/d|w/m/d|m/f/d|f/m/d|all genders)\b", "", t, flags=re.I)
    return safe_filename_part(t, 50)


def retire_file(p: Path) -> Optional[Path]:
    """Move an old CV / letter to Claude CV/_old_versions/<company>/ (never deleted)."""
    try:
        dest_dir = settings.cv_out_dir / "_old_versions" / p.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name
        if dest.exists():
            dest = dest_dir / f"{p.stem}_{datetime.now():%Y%m%d%H%M%S}{p.suffix}"
        shutil.move(str(p), str(dest))
        return dest
    except OSError:
        return None


def target_paths(job: m.Job) -> tuple[Path, str, str]:
    folder = settings.cv_out_dir / safe_folder_name(job.company or "Unknown company")
    from ..config import owner_name
    owner = safe_filename_part(owner_name(), 40)
    comp = safe_filename_part(job.company, 40)
    role = role_for_filename(job.title)
    return folder, f"{owner}_CV_{comp}_{role}", f"{owner}_Cover_Letter_{comp}_{role}"


def _own_docs(job_id: int) -> list[m.Document]:
    with db.session_scope() as s:
        return list(s.exec(select(m.Document).where(m.Document.job_id == job_id)).all())


def free_paths(job: m.Job) -> tuple[Path, str, str]:
    """target_paths, but never on top of another job's files (two jobs with the same title and company,
    or with no company at all, used to share one file name and the second one was never built)."""
    folder, cv_name, letter_name = target_paths(job)
    mine = {p for d in _own_docs(job.id) for p in (d.docx_path, d.pdf_path) if p}

    def taken(*stems: str) -> bool:
        return any((folder / f"{st}{e}").exists() and str(folder / f"{st}{e}") not in mine
                   for st in stems for e in (".docx", ".pdf"))
    if taken(cv_name, letter_name):
        city = safe_filename_part((job.location or "").split(",")[0], 20)
        for tag in ([city] if city else []) + [f"job{job.id}"]:
            if not taken(f"{cv_name}_{tag}", f"{letter_name}_{tag}"):
                return folder, f"{cv_name}_{tag}", f"{letter_name}_{tag}"
    return folder, cv_name, letter_name


def built_files(job_id: int) -> list[str]:
    """This job's own CV files that still exist on disk."""
    return [p for d in _own_docs(job_id) if d.kind == "cv" for p in (d.docx_path, d.pdf_path) if p and Path(p).exists()]


def clean_title(title: str) -> str:
    """'Werkstudent Data (m/w/d) - Berlin' -> 'Werkstudent Data - Berlin' (no gender tags in letters)."""
    from ..textutil import strip_gender
    t = strip_gender(title or "")
    t = re.sub(r"\b(m/w/d|w/m/d|m/f/d|f/m/d|m/w/x|all genders)\b", "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([,–-])\s*$", "", t)).strip(" -–,")


def job_for_writing(job: m.Job, sc: Optional[m.Score]) -> JobForWriting:
    return JobForWriting(
        company=job.company, title=clean_title(job.title), job_type=job.job_type, location=job.location,
        description=job.full_description, keywords=job.keywords, required_skills=job.required_skills,
        missing_wording=sc.missing_wording if sc else [], missing_skills=sc.missing_skills if sc else [],
        deal_breakers=[d["text"] + (" (hard)" if d.get("hard") else "") for d in (sc.deal_breakers if sc else [])])


def _load(job_id: int):
    with db.session_scope() as s:
        job = s.get(m.Job, job_id)
        sc = s.exec(select(m.Score).where(m.Score.job_id == job_id)).first()
    return job, sc


def _profile(state) -> tuple[CVProfile, int]:
    got = cvs.get_profile(state["cv_profile_id"]) if state.get("cv_profile_id") else cvs.get_main()
    if not got:
        raise ValueError("No main CV - upload it on the My CV screen first")
    return got


def _truth(state) -> CVProfile:
    return CVProfile.model_validate(state["tprof"]) if state.get("tprof") else _profile(state)[0]


def _writer(state) -> CVProfile:
    return CVProfile.model_validate(state["wprof"]) if state.get("wprof") else _profile(state)[0]


def _jw(state, job: m.Job, sc) -> JobForWriting:
    """The job for the writers. A 'missing' skill that your picked GitHub project shows is not missing."""
    jw = job_for_writing(job, sc)
    if state.get("tprof"):
        from ..docs.ats import hit
        from ..steps.score import profile_text
        have = profile_text(_truth(state))
        jw.missing_skills = [t for t in jw.missing_skills if not hit(t, have)]
    return jw


def _emit(state, msg, **kw):
    events.emit({"type": "build", "message": msg, "job_id": state.get("current"), **kw}, run_id=state.get("run_id"))


def candidates(min_score: Optional[int] = None) -> list[dict]:
    min_score = settings.build_min_score if min_score is None else min_score
    with db.session_scope() as s:
        rows = s.exec(select(m.Job, m.Score).join(m.Score, m.Score.job_id == m.Job.id)
                      .where(m.Score.total >= min_score, m.Job.status == m.JobStatus.SCORED)).all()
    return [{"job_id": j.id, "company": j.company, "title": j.title, "score": sc.total, "verdict": sc.verdict}
            for j, sc in sorted(rows, key=lambda t: -t[1].total)]


def _short_issue(c) -> str:
    if c.name == "Exactly 1 page":
        return f"too long for one page ({c.detail}), shortening"
    if c.name.startswith("Only tools"):
        return f"tools not on your CV ({c.detail})"
    if c.name.startswith("Only numbers"):
        return f"numbers not on your CV ({c.detail})"
    return f"{c.name}" + (f" ({c.detail})" if c.detail else "")


def _level_key(state) -> str:
    _, vid = _profile(state)
    return f"fit_level:{vid}"


def _start_level(state) -> int:
    """Start at the length that fitted one page last time for this CV, instead of the longest version."""
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, _level_key(state))
            return max(0, int(row.value) - 1) if row else 0
    except Exception:
        return 0


def _remember_level(state, level: int) -> None:
    try:
        with db.session_scope() as s:
            key = _level_key(state)
            row = s.get(m.AppSetting, key) or m.AppSetting(key=key)
            row.value = str(int(level))
            s.add(row)
    except Exception:
        pass


# ------------------------------------------------------------------ nodes ---
def review_jobs(state: BuildState) -> dict:
    ids = state.get("job_ids")
    if not ids:
        cands = candidates()
        events.emit({"type": "review", "message": f"{len(cands)} jobs ready - tick the ones to build",
                     "candidates": cands}, run_id=state["run_id"])
        _set_run(state["run_id"], status="paused")
        ids = interrupt({"candidates": cands})
        _set_run(state["run_id"], status="running")
    ids = [int(i) for i in (ids or [])]
    return {"job_ids": ids, "queue": list(ids), "tmp_dir": tempfile.mkdtemp(prefix="jobagent_build_")}


def next_job(state: BuildState) -> dict:
    queue = list(state.get("queue", []))
    results = []
    while queue:
        jid = queue.pop(0)
        job, sc = _load(jid)
        if not job:
            continue
        if sc and sc.total < settings.build_min_score and not state.get("rebuild"):
            results.append({"job_id": jid, "status": "skipped", "reason": f"Score {sc.total} below {settings.build_min_score}"})
            continue
        have = built_files(jid)
        if have and not state.get("rebuild"):
            results.append({"job_id": jid, "status": "already built", "folder": str(Path(have[0]).parent),
                            "cv_file": Path(have[0]).name})
            events.emit({"type": "build", "message": f"{job.company or 'This job'} – {job.title}: already built "
                         f"({Path(have[0]).name}). Use Rebuild to make a new version.", "job_id": jid}, run_id=state["run_id"])
            continue
        events.emit({"type": "build", "message": f"Building CV for {job.company} – {job.title}", "job_id": jid},
                    run_id=state["run_id"])
        return {"queue": queue, "current": jid, "cv": None, "letter": None, "issues": [], "attempts": 0,
                "level": _start_level(state),
                "qa_cv": [], "qa_letter": [], "letter_attempts": 0, "results": results}
    return {"queue": [], "current": None, "results": results}


def route_next(state: BuildState) -> str:
    return "prepare" if state.get("current") else "finish"


def prepare(state: BuildState) -> dict:
    """Pick the projects (main CV + GitHub), the availability line and the 'before' ATS score."""
    from ..cv import projects as P
    from ..docs import ats
    prof, _ = _profile(state)
    job, sc = _load(state["current"])
    jw = job_for_writing(job, sc)
    picked, info = P.pick(jw, prof)          # a shortlist: what you asked for plus two spares
    gi = info.get("github") or {}
    if gi.get("error"):
        _emit(state, f"GitHub: {gi['error']} - using the projects on your CV")
    _emit(state, f"Project shortlist (best {info.get('want')} go on the CV): " + "; ".join(f"{x.item.name} ({'GitHub' if x.source == 'github' else 'CV + GitHub' if x.source == 'cv+github' else 'CV'})"
                                          for x in picked))
    fact = ats.availability_fact(job.job_type) if settings.state_availability else ""
    w = P.writer_profile(prof, picked)
    t = P.truth_profile(prof, picked)
    if fact:
        t.summary = (t.summary + " " + fact).strip()      # its numbers (hours, year) are allowed in the CV
    before = ats.score(jw, prof, prof)
    _emit(state, f"ATS score of your main CV for this job: {before['score']}")
    return {"wprof": w.model_dump(), "tprof": t.model_dump(), "picked": [x.as_dict() for x in picked],
            "proj_info": info, "fact": fact, "ats_before": before, "ats_after": {}, "review": None, "reviewed": False}


def write_cv(state: BuildState) -> dict:
    llm.set_current_run(state["run_id"])
    prof = _writer(state)
    job, sc = _load(state["current"])
    cv = W.write_cv(_jw(state, job, sc), prof, state.get("issues") or None, level=state.get("level", 0),
                    picked=(state.get("proj_info") or {}).get("want", 0) if state.get("wprof") else 0,
                    fact=state.get("fact", ""))
    cv = W.limit_em_dashes(cv)
    return {"cv": cv.model_dump(), "attempts": state.get("attempts", 0) + 1}


def check_cv(state: BuildState) -> dict:
    prof = _truth(state)
    job, sc = _load(state["current"])
    res = W.check_cv(_jw(state, job, sc), prof, CVContent.model_validate(state["cv"]))
    return {"issues": res.issues if not res.passed else []}


def _trim_once(cv: CVContent, kw: dict, n: int, min_projects: int) -> int:
    """Cut up to n lines from the CV without Claude: the bullets that carry the fewest job keywords, from the
    items with the most bullets (each item keeps at least one), then the last items of the longest skill group.
    Returns how many cuts were made."""
    from ..docs.ats import hit

    def value(b: str) -> int:
        return sum(w for t, w in kw.items() if hit(t, b))
    cuts = 0
    for _ in range(n):
        items = [(it, 2) for it in cv.experience] + [(it, 1) for it in cv.projects[:max(min_projects, 1)]] \
            + [(it, 1) for it in cv.projects[max(min_projects, 1):]]
        cand = [(it, keep) for it, keep in items if len(it.bullets) > keep]
        if cand:
            it = max(cand, key=lambda x: (len(x[0].bullets), -min(value(b) for b in x[0].bullets)))[0]
            weakest = min(range(len(it.bullets)), key=lambda i: (value(it.bullets[i]), -len(it.bullets[i]), -i))
            it.bullets.pop(weakest)
            cuts += 1
            continue
        groups = [g for g in cv.skills if len(g.items) > 4]
        if groups:
            g = max(groups, key=lambda g: len(g.items))
            weakest = min(range(len(g.items)), key=lambda i: (value(g.items[i]), -i))
            g.items.pop(weakest)
            cuts += 1
            continue
        break
    return cuts


def render_qa(state: BuildState) -> dict:
    from ..docs import ats
    prof = _truth(state)
    job, sc = _load(state["current"])
    jw = _jw(state, job, sc)
    wanted = (state.get("proj_info") or {}).get("want") or 3
    style = cvs.active_style()
    cv = CVContent.model_validate(state["cv"])
    tmp = Path(state["tmp_dir"]) / f"job{state['current']}"
    tmp.mkdir(parents=True, exist_ok=True)
    checks = []
    for compact in (0, 1, 2):          # tighter spacing before asking the writer to cut text
        d = render_cv(cv, prof, style, tmp / "cv.docx", compact=compact)
        try:
            pdf = to_pdf(d)
        except PdfUnavailable as e:
            pdf = None
            checks = qa.check_cv(cv, prof, d, None, style, wanted) + [qa.Check("PDF created", False, str(e))]
            break
        checks = qa.check_cv(cv, prof, d, pdf, style, wanted)
        one_page = next((c for c in checks if c.name == "Exactly 1 page"), None)
        if one_page is None or one_page.passed:
            break
    # still too long: shorten it here (seconds, no tokens) instead of a whole new Claude rewrite (minutes, tokens)
    trimmed = 0
    kw = ats.keywords(jw)
    least = min(3, wanted)
    for _ in range(12):
        one_page = next((c for c in checks if c.name == "Exactly 1 page"), None)
        if pdf is None or one_page is None or one_page.passed:
            break
        over = qa.overflow_lines(pdf)
        made = _trim_once(cv, kw, max(1, over // 2), least)
        if not made:
            break
        trimmed += made
        d = render_cv(cv, prof, style, tmp / "cv.docx", compact=2)
        pdf = to_pdf(d)
        checks = qa.check_cv(cv, prof, d, pdf, style, wanted)
    if trimmed:
        _emit(state, f"Shortened the CV to one page without a rewrite ({trimmed} line(s) cut: the bullets with the "
                     f"fewest job keywords)" if all(c.passed for c in checks if c.name == "Exactly 1 page")
              else f"Cut {trimmed} line(s), still too long")
    after = ats.score(jw, cv, prof)
    pdf_text = None
    if pdf is not None:
        try:
            from pypdf import PdfReader
            pdf_text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(pdf)).pages)
        except Exception:
            pdf_text = None
    checks += ats.screen_checks(jw, cv, prof, prof, after, settings.ats_target, state.get("fact", ""), pdf_text,
                                state.get("ats_before"))
    issues = list(state.get("issues", [])) + qa.issues(checks)
    level = state.get("level", 0)
    if any(c.name == "Exactly 1 page" and not c.passed for c in checks):
        level += 1
    _emit(state, f"CV attempt {state.get('attempts', 1)}: " + ("all checks passed" if not issues else
                 "; ".join(_short_issue(c) for c in checks if not c.passed and not c.soft) or "; ".join(issues)[:200])
          + ("" if not issues or state.get("attempts", 1) >= settings.max_build_attempts else " – rewriting"))
    _emit(state, f"ATS score: {state.get('ats_before', {}).get('score', '?')} → {after['score']}")
    return {"qa_cv": [c.as_dict() for c in checks], "issues": issues, "level": level, "ats_after": after,
            "cv": cv.model_dump()}


def route_cv(state: BuildState) -> str:
    nxt = "review" if settings.recruiter_review and not state.get("reviewed") else "write_letter"
    if not state.get("issues"):
        return nxt
    if state.get("attempts", 0) >= settings.max_build_attempts:
        return nxt                # saved but flagged "needs your attention"
    return "write_cv"


def review(state: BuildState) -> dict:
    """A recruiter's look at the finished CV: what would get it rejected, and the true fixes."""
    llm.set_current_run(state["run_id"])
    job, sc = _load(state["current"])
    try:
        r = W.review_cv(_jw(state, job, sc), _truth(state), CVContent.model_validate(state["cv"]),
                        state.get("ats_after") or {})
    except Exception as e:                       # the review is extra: never fail a build on it
        log.warning("recruiter review failed: %s", e)
        return {"reviewed": True}
    msg = f"Recruiter review: {r.verdict}" + (f" - {r.reason}" if r.reason else "")
    if r.knockouts:
        msg += " | Knock-out: " + "; ".join(r.knockouts[:3])
    _emit(state, msg)
    out = {"reviewed": True, "review": r.model_dump()}
    if r.fixes and r.verdict != "interview":
        _emit(state, "Applying the reviewer's fixes: " + "; ".join(r.fixes[:5]))
        out["issues"] = list(r.fixes[:5])
    return out


def route_review(state: BuildState) -> str:
    r = state.get("review") or {}
    return "write_cv" if r.get("fixes") and r.get("verdict") != "interview" and state.get("issues") else "write_letter"


def write_letter(state: BuildState) -> dict:
    llm.set_current_run(state["run_id"])
    prof = _truth(state)
    job, sc = _load(state["current"])
    issues = [c["detail"] or c["name"] for c in state.get("qa_letter", []) if not c["passed"]]
    L = W.write_letter(_jw(state, job, sc), prof, CVContent.model_validate(state["cv"]), issues or None)
    return {"letter": L.model_dump(), "letter_attempts": state.get("letter_attempts", 0) + 1}


def letter_qa(state: BuildState) -> dict:
    prof = _truth(state)
    style = cvs.active_style()
    job, _ = _load(state["current"])
    L = LetterContent.model_validate(state["letter"])
    cv = CVContent.model_validate(state["cv"])
    tmp = Path(state["tmp_dir"]) / f"job{state['current']}"
    d = render_letter(L, prof, cv.tagline, style, tmp / "letter.docx", company=job.company)
    try:
        pdf = to_pdf(d)
    except PdfUnavailable:
        pdf = None
    checks = qa.check_letter(L, prof, pdf, style)
    return {"qa_letter": [c.as_dict() for c in checks]}


def route_letter(state: BuildState) -> str:
    ok = all(c["passed"] for c in state.get("qa_letter", []))
    return "save" if ok or state.get("letter_attempts", 0) >= 2 else "write_letter"


def used_projects(state) -> list[dict]:
    """The shortlist entries that ended up on the CV, in CV order (sources for the screen)."""
    by = {d["name"]: d for d in state.get("picked") or []}
    out = [by[p["name"]] for p in (state.get("cv") or {}).get("projects", []) if p["name"] in by]
    names = {d["name"] for d in out}
    return out + [{**d, "unused": True} for d in state.get("picked") or [] if d["name"] not in names and d.get("item")]


def save(state: BuildState) -> dict:
    jid = state["current"]
    job, sc = _load(jid)
    _, vid = _profile(state)
    folder, cv_name, letter_name = free_paths(job)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = Path(state["tmp_dir"]) / f"job{jid}"
    files = {}
    for kind, stem, base in (("cv", "cv", cv_name), ("letter", "letter", letter_name)):
        for ext in (".docx", ".pdf"):
            src = tmp / f"{stem}{ext}"
            if src.exists():
                dst = folder / f"{base}{ext}"
                shutil.copy2(src, dst)
                files[f"{kind}{ext}"] = str(dst)
    cv_ok = qa.passed_dicts(state.get("qa_cv", []))
    letter_ok = qa.passed_dicts(state.get("qa_letter", []))
    meta = {"_ats": {"before": state.get("ats_before") or {}, "after": state.get("ats_after") or {},
                     "target": settings.ats_target},
            "_projects": used_projects(state), "_review": state.get("review") or None,
            "_fact": state.get("fact", ""),
            "_settings": {"model": settings.cv_model or "default", "projects": settings.cv_projects,
                          "github": settings.github_projects, "lean": settings.claude_lean}}
    L = LetterContent.model_validate(state["letter"])
    with db.session_scope() as s:
        keep = set(files.values())
        for old in s.exec(select(m.Document).where(m.Document.job_id == jid)).all():
            for p in (old.docx_path, old.pdf_path):
                if p and p not in keep and Path(p).exists():
                    retire_file(Path(p))            # an earlier version of this job's CV/letter
            s.delete(old)
        s.add(m.Document(job_id=jid, cv_version_id=vid, kind="cv", docx_path=files.get("cv.docx", ""),
                         pdf_path=files.get("cv.pdf", ""), content={**state["cv"], **meta}, qa_results=state.get("qa_cv", []),
                         qa_passed=cv_ok, attempts=state.get("attempts", 1)))
        s.add(m.Document(job_id=jid, cv_version_id=vid, kind="letter", docx_path=files.get("letter.docx", ""),
                         pdf_path=files.get("letter.pdf", ""), content=state["letter"],
                         qa_results=state.get("qa_letter", []), qa_passed=letter_ok,
                         blockers_stated=L.blockers_stated, attempts=state.get("letter_attempts", 1)))
        j = s.get(m.Job, jid)
        if j.status in (m.JobStatus.SCORED, m.JobStatus.NEW, m.JobStatus.BUILT):
            j.status = m.JobStatus.BUILT
        s.add(j)
    if cv_ok:
        _remember_level(state, state.get("level", 0))
    status = "built" if cv_ok and letter_ok else "built - needs your attention"
    failed = [c["name"] + (f" ({c['detail']})" if c["detail"] else "") for c in state.get("qa_cv", []) + state.get("qa_letter", [])
              if not c["passed"] and not c.get("soft")]
    _emit(state, f"Saved {Path(files.get('cv.pdf', files.get('cv.docx', ''))).name}" + (f" - check: {'; '.join(failed)}" if failed else ""))
    return {"results": [{"job_id": jid, "status": status, "folder": str(folder),
                         "cv_file": Path(files.get("cv.docx", "")).name,
                         "letter_file": Path(files.get("letter.docx", "")).name,
                         "blockers": L.blockers_stated, "failed_checks": failed,
                         "projects": [p["name"] for p in state["cv"]["projects"]],
                         "ats_before": (state.get("ats_before") or {}).get("score"),
                         "ats_after": (state.get("ats_after") or {}).get("score")}],
            "current": None}


REPORT_COLS = ["Score", "Company", "Job Title", "Job Type", "Location", "Verdict", "Status",
               "Blockers stated in letter", "Failed checks", "CV version", "Projects used", "Folder", "CV file",
               "Cover letter file", "Job Link"]


def write_report(results: list[dict], day: Optional[date] = None) -> Path:
    day = day or date.today()
    path = settings.cv_out_dir / f"_Report_{day.isoformat()}.xlsx"
    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = f"Batch {day.isoformat()}"
        ws.append(REPORT_COLS)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1B2A4A")
        for col, w in zip("ABCDEFGHIJKLMNO", (7, 28, 40, 16, 24, 22, 22, 40, 40, 10, 40, 30, 50, 50, 45)):
            ws.column_dimensions[col].width = w
    main = cvs.get_main()
    version = ""
    if main:
        with db.session_scope() as s:
            v = s.get(m.CVVersion, main[1])
            version = f"v{v.version}" if v else ""
    for r in results:
        job, sc = _load(r["job_id"])
        if not job:
            continue
        ws.append([sc.total if sc else None, job.company, job.title, job.job_type, job.location,
                   sc.verdict if sc else "", r["status"] + (f" ({r['reason']})" if r.get("reason") else ""),
                   "; ".join(r.get("blockers", [])), "; ".join(r.get("failed_checks", [])), version,
                   ", ".join(r.get("projects", [])), r.get("folder", ""), r.get("cv_file", ""),
                   r.get("letter_file", ""), job.link])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def finish(state: BuildState) -> dict:
    results = state.get("results", [])
    path = write_report(results)
    counts = {}
    for r in results:
        key = r["status"].split(" - ")[0]
        counts[key] = counts.get(key, 0) + 1
    if state.get("tmp_dir"):
        shutil.rmtree(state["tmp_dir"], ignore_errors=True)
    cost = llm.cost_tracker.run_cost.get(state["run_id"], 0.0)
    _set_run(state["run_id"], status="done", finished_at=datetime.now().astimezone(), counts=counts,
             cost_usd=round(cost, 4))
    events.emit({"type": "run_done", "kind": "build", "message": "Build finished: " + (", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing to build"), "report": str(path)},
                run_id=state["run_id"])
    return {"report_path": str(path)}


def _set_run(run_id: int, **fields) -> None:
    with db.session_scope() as s:
        r = s.get(m.Run, run_id)
        if r:
            for k, v in fields.items():
                setattr(r, k, v)
            s.add(r)


def build_graph(checkpointer=None):
    g = StateGraph(BuildState)
    for name, fn in (("review", review_jobs), ("next_job", next_job), ("prepare", prepare), ("write_cv", write_cv),
                     ("check_cv", check_cv), ("render_qa", render_qa), ("recruiter", review),
                     ("write_letter", write_letter), ("letter_qa", letter_qa), ("save", save), ("finish", finish)):
        g.add_node(name, fn)
    g.add_edge(START, "review")
    g.add_edge("review", "next_job")
    g.add_conditional_edges("next_job", route_next, ["prepare", "finish"])
    g.add_edge("prepare", "write_cv")
    g.add_edge("write_cv", "check_cv")
    g.add_edge("check_cv", "render_qa")
    g.add_conditional_edges("render_qa", route_cv, {"write_cv": "write_cv", "review": "recruiter", "write_letter": "write_letter"})
    g.add_conditional_edges("recruiter", route_review, ["write_cv", "write_letter"])
    g.add_edge("write_letter", "letter_qa")
    g.add_conditional_edges("letter_qa", route_letter, ["write_letter", "save"])
    g.add_edge("save", "next_job")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


async def _invoke(inp: Any, run_id: int) -> dict:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    thread = {"configurable": {"thread_id": f"build-{run_id}"}, "recursion_limit": 2000}
    llm.set_current_run(run_id)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
        graph = build_graph(saver)
        out = await graph.ainvoke(inp, thread)
        snap = await graph.aget_state(thread)
    out = dict(out)
    out["run_id"] = run_id
    out["paused"] = bool(snap.next)
    return out


async def start_build(job_ids: Optional[list[int]] = None, cv_profile_id: Optional[int] = None,
                      rebuild: bool = False, run_id: Optional[int] = None) -> dict:
    if run_id is None:
        with db.session_scope() as s:
            r = m.Run(kind="build", status="running")
            s.add(r)
            s.flush()
            run_id = r.id
            r.thread_id = f"build-{run_id}"
            s.add(r)
    events.emit({"type": "run_started", "kind": "build", "message": "Build started"}, run_id=run_id)
    try:
        return await _invoke({"run_id": run_id, "job_ids": job_ids or [], "cv_profile_id": cv_profile_id,
                              "rebuild": rebuild, "results": []}, run_id)
    except Exception as e:
        log.exception("build failed")
        _set_run(run_id, status="failed", finished_at=datetime.now().astimezone(), errors=[str(e)[:500]])
        events.emit({"type": "run_failed", "kind": "build", "message": f"Build failed: {e}"}, run_id=run_id)
        raise


async def resume_build(run_id: int, job_ids: list[int]) -> dict:
    """Continue a paused build with the jobs you ticked."""
    return await _invoke(Command(resume=job_ids), run_id)
