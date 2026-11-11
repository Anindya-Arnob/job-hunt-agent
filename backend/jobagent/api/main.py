"""FastAPI backend: REST endpoints for every screen, live events over WebSocket,
and the built React app served at /."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import func, or_, select

from .. import db, events, llm, models as m
from ..browser import connections
from ..config import PROJECT_ROOT, settings
from ..cv import service as cvs
from ..docs import qa
from ..docs.pdf import PdfUnavailable, to_pdf
from ..docs.render import render_cv, render_letter
from ..graphs.build_graph import candidates as build_candidates
from ..schemas import CVContent, CVProfile, LetterContent
from ..steps.export import export_all, top_missing_keywords
from ..steps.score import breakdown_text
from . import runs
from .. import scheduler

log = logging.getLogger(__name__)
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    try:
        from ..steps.clean import hide_junk_jobs
        hide_junk_jobs()
    except Exception as e:  # never block the start
        logging.getLogger(__name__).warning("junk cleanup: %s", e)
    scheduler.start()
    from ..sync_docs import sync_all
    asyncio.get_running_loop().run_in_executor(None, sync_all)   # match CV files on disk in the background
    yield
    scheduler.stop()


app = FastAPI(title="Job Hunt Agent", version="1.0", lifespan=lifespan)


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


# ------------------------------------------------------------------ helpers ---
def job_dict(j: m.Job, sc: Optional[m.Score] = None, full: bool = False) -> dict:
    d = {
        "id": j.id, "company": j.company, "title": j.title, "job_type": j.job_type, "role_category": j.role_category,
        "location": j.location, "work_mode": j.work_mode,
        "posted_date": j.posted_date.isoformat() if j.posted_date else None, "sources": j.sources,
        "link": j.link, "links": j.links, "status": j.status, "exclude_reason": j.exclude_reason, "notes": j.notes,
        "language_req": j.language_req, "salary": j.salary, "deadline": j.deadline,
        "first_seen": j.created_at.isoformat() if j.created_at else None,
        "score": sc.total if sc else None, "verdict": sc.verdict if sc else None,
        "score_outdated": sc.is_outdated if sc else False,
        "deal_breakers": sc.deal_breakers if sc else [],
        "has_description": bool(j.full_description),
    }
    if full:
        d.update({
            "full_description": j.full_description, "required_skills": j.required_skills, "keywords": j.keywords,
            "breakdown": ({"skills": sc.skills, "experience": sc.experience, "title_fit": sc.title_fit,
                           "education": sc.education, "language": sc.language, "type_location": sc.type_location,
                           "text": breakdown_text(sc)} if sc else None),
            "missing_wording": sc.missing_wording if sc else [], "missing_skills": sc.missing_skills if sc else [],
            "rationale": sc.rationale if sc else "",
        })
    return d


def _safe_path(p: str) -> Path:
    path = Path(p).resolve()
    roots = [Path(settings.job_dir).resolve(), Path(settings.data_dir).resolve()]
    if not any(str(path).startswith(str(r)) for r in roots):
        raise HTTPException(403, "Path outside the job folder")
    if not path.exists():
        raise HTTPException(404, "File not found")
    return path


def profile_to_content(p: CVProfile) -> CVContent:
    return CVContent(tagline=p.tagline, summary=p.summary, skills=p.skills, experience=p.experience,
                     projects=p.projects or [], education=p.education, certifications=p.certifications,
                     languages=p.languages)


# ------------------------------------------------------------------- status ---
@app.get("/api/status")
def status():
    main = cvs.get_main()
    return {"settings": settings.public_dict(), "has_main_cv": bool(main),
            "running": runs.running(), "today": date.today().isoformat(),
            "pdf_available": __import__("jobagent.docs.pdf", fromlist=["find_soffice"]).find_soffice() is not None
            or __import__("sys").platform == "win32"}


GOOD = ("Apply", "Apply after CV tailoring")
BOARD_HOSTS = re.compile(r"stepstone|linkedin|indeed|glassdoor|xing|wellfound|arbeitnow|remoteok|arbeitsagentur|"
                         r"join\.com|greenhouse|lever\.co|personio|recruitee|workday|smartrecruiters|softgarden|ashby|"
                         r"google\.|bing\.|monster|jobware|kimeta|jooble|careerjet|talent\.com", re.I)


def _company_or_hint(j: m.Job) -> str:
    """The company, or where it came from when the posting did not name it."""
    if j.company:
        return j.company
    from urllib.parse import urlparse
    host = urlparse(j.link or "").netloc.lower().removeprefix("www.")
    if host and not BOARD_HOSTS.search(host):
        parts = [p for p in host.split(".") if p not in ("jobs", "careers", "career", "karriere", "join", "apply", "de", "com", "io", "eu", "net", "org")]
        if parts:
            return parts[-1].capitalize()
    src = (j.sources or [""])[0].split(":")[-1]
    names = {"xing": "XING", "linkedin": "LinkedIn", "stepstone": "StepStone", "imported": "earlier Excel",
             "websearch": "web search", "web": "web search", "inbox": "your inbox"}
    return f"via {names.get(src, src.capitalize())}" if src else ""


from ..textutil import norm_title  # noqa: E402


def _cv_first_name() -> str:
    got = cvs.get_main()
    return ((getattr(got[0], "name", "") or "").split(" ") or [""])[0] if got else ""


@app.get("/api/dashboard")
def dashboard():
    from collections import Counter
    from ..apply import profile as AP
    now = datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=6)
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job)).all()
        scores = {sc.job_id: sc for sc in s.exec(select(m.Score)).all()}
        last = s.exec(select(m.Run).where(m.Run.kind == "search").order_by(m.Run.id.desc())).first()
        stats = s.exec(select(m.SearchStat).where(m.SearchStat.run_id == last.id)).all() if last else []
        apps = {a.job_id: a for a in s.exec(select(m.Application)).all()}
        paused = s.exec(select(m.Run).where(m.Run.status == "paused")).all()
        with_cv = {jid for jid, kind in s.exec(select(m.Document.job_id, m.Document.kind)).all() if kind == "cv"}
        mails = s.exec(select(m.MailEvent).order_by(m.MailEvent.received_at.desc()).limit(400)).all()
        attempts_today = s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.status == "applied")).all()

    def local(d):
        if not d:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone()

    good = lambda j: j.id in scores and scores[j.id].verdict in GOOD and j.status != "excluded"
    APPLIED = ("applied", "interview", "offer", "rejected")
    by_status = Counter(j.status for j in jobs)
    applied_jobs = [j for j in jobs if j.status in APPLIED]
    applied_on = {j.id: (apps[j.id].applied_at if j.id in apps and apps[j.id].applied_at else (local(j.updated_at).date() if j.updated_at else None))
                  for j in applied_jobs}
    applied_today = sum(1 for d in applied_on.values() if d == today.date())
    responses = by_status["interview"] + by_status["offer"] + by_status["rejected"]

    # last 14 days
    days = [(today - timedelta(days=k)).date() for k in range(13, -1, -1)]
    found_by = Counter(local(j.created_at).date() for j in jobs if j.created_at and j.status != "excluded")
    good_by = Counter(local(j.created_at).date() for j in jobs if j.created_at and good(j))
    appl_by = Counter(d for d in applied_on.values() if d)
    activity = [{"date": d.isoformat(), "found": found_by.get(d, 0), "good": good_by.get(d, 0), "applied": appl_by.get(d, 0)}
                for d in days]

    def row(j):
        d = job_dict(j, scores.get(j.id))
        d["company"] = _company_or_hint(j)
        d["has_cv"] = j.id in with_cv
        sc = scores.get(j.id)
        d["blockers"] = [{"text": b.get("text", "").replace("BLOCKER: ", ""), "hard": bool(b.get("hard"))}
                         for b in (sc.deal_breakers if sc else [])
                         if b.get("hard") or not re.search(r"enrolled as a student|you are: .{0,20}student", b.get("text", ""), re.I)][:2]
        d["missing"] = (sc.missing_skills if sc else [])[:3]
        return d

    def uniq(lst):                                   # the same posting found twice: show it once
        seen, out = set(), []
        for j in lst:
            key = (norm_title(j.title), (j.company or "").lower())
            if key not in seen:
                seen.add(key)
                out.append(j)
        return out
    to_act = uniq(sorted([j for j in jobs if j.status == "scored" and good(j)], key=lambda j: -scores[j.id].total))
    ready = uniq(sorted([j for j in jobs if j.status == "built" and good(j)], key=lambda j: -scores[j.id].total))
    src = Counter()
    for j in jobs:
        if good(j):
            for x in (j.sources or ["other"])[:1]:
                src[x.split(":")[0] if x.startswith("email") else x] += 1
    types = Counter((j.job_type or "Unclear") for j in jobs if good(j))

    # inbox
    try:
        from ..mail import service as mail
        mstat = mail.public()
    except Exception:
        mstat = {"has_password": False}
    week_mail = Counter(e.kind for e in mails if e.received_at and local(e.received_at) >= week_ago)
    todo = [e for e in mails if e.kind in ("interview", "assessment", "offer") and not e.done]
    recent = [e for e in mails if e.kind in ("confirmation", "rejection", "interview", "assessment", "offer", "update")][:6]
    ev = lambda e: {"id": e.id, "kind": e.kind, "company": e.company or e.sender_name, "role": e.role, "summary": e.summary,
                    "action": e.action, "when": e.when_text, "at": local(e.received_at).isoformat() if e.received_at else None}

    from ..feeds.websearch import claude_cli
    mode = settings.effective_llm_mode
    ai = ("Claude API" if mode != "fake" else ("Claude Code (your Claude subscription)" if claude_cli() else "Offline rules (no Claude)"))
    prof = AP.load()
    warnings = [{"site": st.site, "query": st.query, "location": st.location, "status": st.status, "error": st.error}
                for st in stats if st.status not in ("ok", "split")]
    due = [a for a in apps.values() if a.follow_up_at and a.follow_up_at <= date.today() and not a.outcome]
    nxt = scheduler.next_run()
    return {
        "name": prof.first_name or _cv_first_name(),
        "ai": ai, "llm_mode": mode, "cost_today": round(llm.cost_tracker.day_cost, 3),
        "next_search": nxt.isoformat() if nxt else None,
        "new_today": sum(1 for j in jobs if j.created_at and local(j.created_at) >= today and j.status != "excluded"),
        "new_week": sum(found_by.get(d, 0) for d in days[-7:]),
        "good_week": sum(good_by.get(d, 0) for d in days[-7:]),
        "kpi": {"to_act": len(to_act), "ready": len(ready), "applied": len(applied_jobs),
                "applied_week": sum(appl_by.get(d, 0) for d in days[-7:]), "applied_today": applied_today,
                "daily_limit": settings.apply_daily_limit, "interviews": by_status["interview"] + by_status["offer"],
                "offers": by_status["offer"], "rejected": by_status["rejected"], "responses": responses,
                "response_rate": round(100 * responses / len(applied_jobs)) if applied_jobs else 0,
                "needs_review": by_status["needs_review"], "follow_ups_due": len(due)},
        "funnel": [
            {"key": "found", "label": "Found", "count": len(jobs)},
            {"key": "relevant", "label": "Relevant", "count": len(jobs) - by_status["excluded"]},
            {"key": "good", "label": "Good match", "count": sum(1 for j in jobs if good(j))},
            {"key": "cv", "label": "CV built", "count": sum(1 for j in jobs if j.id in with_cv)},
            {"key": "applied", "label": "Applied", "count": len(applied_jobs)},
            {"key": "interview", "label": "Interview", "count": by_status["interview"] + by_status["offer"]},
            {"key": "offer", "label": "Offer", "count": by_status["offer"]},
        ],
        "activity": activity,
        "top": [row(j) for j in to_act[:8]],
        "ready_list": [row(j) for j in ready[:5]],
        "sources": src.most_common(8),
        "types": types.most_common(6),
        "inbox": {"connected": bool(mstat.get("has_password")), "last_check": mstat.get("last_check"),
                  "week": dict(week_mail), "todo": [ev(e) for e in todo[:4]], "todo_count": len(todo),
                  "recent": [ev(e) for e in recent]},
        "counts": {k: by_status.get(k, 0) for k in m.JobStatus.ALL},
        "last_run": _run_dict(last) if last else None,
        "coverage_warnings": warnings[:30],
        "coverage_warning_count": len(warnings),
        "follow_ups_due": len(due),
        "paused_runs": [_run_dict(r) for r in paused],
        "has_main_cv": bool(cvs.get_main()),
    }


def _run_dict(r: m.Run) -> dict:
    return {"id": r.id, "kind": r.kind, "status": r.status, "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None, "cost_usd": r.cost_usd,
            "counts": r.counts, "errors": r.errors, "filter_profile_id": r.filter_profile_id}


# ------------------------------------------------------------------ filters ---
class FilterIn(BaseModel):
    name: str
    roles: list[str] = []
    keywords_in: list[str] = []
    keywords_out: list[str] = []
    job_types: list[str] = []
    locations: list[str] = []
    work_modes: list[str] = []
    days: int = 5
    sources: list[str] = []
    language_max: str = "any"
    exclude_companies: list[str] = []
    min_salary: int = 0
    is_default: bool = False


@app.get("/api/filters/options")
def filter_options():
    from ..browser.connections import list_connections
    return {"roles": db.ALL_ROLES, "job_types": db.ALL_JOB_TYPES, "locations": db.ALL_LOCATIONS,
            "work_modes": ["Remote", "Hybrid", "On-site"], "days": [1, 3, 5, 7],
            "language_levels": ["any", "none", "A1", "A2", "B1", "B2", "C1"],
            "sources": [{"id": c["site"], "label": c["label"]} for c in list_connections()]}


@app.get("/api/filters")
def list_filters():
    with db.session_scope() as s:
        return [f.model_dump() for f in s.exec(select(m.FilterProfile)).all()]


@app.post("/api/filters")
def create_filter(f: FilterIn):
    with db.session_scope() as s:
        row = m.FilterProfile(**f.model_dump())
        s.add(row)
        s.flush()
        return row.model_dump()


@app.put("/api/filters/{fid}")
def update_filter(fid: int, f: FilterIn):
    with db.session_scope() as s:
        row = s.get(m.FilterProfile, fid)
        if not row:
            raise HTTPException(404)
        for k, v in f.model_dump().items():
            setattr(row, k, v)
        s.add(row)
        return row.model_dump()


@app.delete("/api/filters/{fid}")
def delete_filter(fid: int):
    with db.session_scope() as s:
        row = s.get(m.FilterProfile, fid)
        if row:
            s.delete(row)
    return {"ok": True}


# --------------------------------------------------------------------- runs ---
class SearchIn(BaseModel):
    filter_profile_id: int


@app.post("/api/runs/search")
async def start_search(body: SearchIn):
    try:
        return {"run_id": await runs.start_search(body.filter_profile_id)}
    except runs.Busy as e:
        _err(e, 409)


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: int):
    return {"run_id": await runs.resume_search(run_id)}


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: int):
    return {"ok": await runs.cancel(run_id)}


class CaptchaIn(BaseModel):
    site: str


@app.post("/api/runs/{run_id}/captcha-solved")
def captcha_solved(run_id: int, body: CaptchaIn):
    events.signal(f"captcha:{run_id}:{body.site}")
    return {"ok": True}


@app.get("/api/runs")
def list_runs(limit: int = 50):
    with db.session_scope() as s:
        rows = s.exec(select(m.Run).order_by(m.Run.id.desc()).limit(limit)).all()
        return [_run_dict(r) for r in rows]


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    with db.session_scope() as s:
        r = s.get(m.Run, run_id)
        if not r:
            raise HTTPException(404)
        stats = s.exec(select(m.SearchStat).where(m.SearchStat.run_id == run_id)).all()
    by_site: dict[str, dict] = {}
    for st in stats:
        d = by_site.setdefault(st.site, {"site": st.site, "searches": 0, "guest_ok": 0, "needed_login": 0,
                                          "failed": 0, "human_check": 0, "split": 0, "collected": 0, "web": 0,
                                          "web_jobs": 0})
        d["searches"] += 1
        d["collected"] += st.collected
        if st.mode == "web":
            d["web"] += 1
            d["web_jobs"] += st.collected
        elif st.status in ("ok", "limit") and st.mode in ("guest", "feed", "window"):
            d["guest_ok"] += 1
        if st.mode == "login":
            d["needed_login"] += 1
        if st.status in ("captcha", "manual"):
            d["human_check"] += 1
        elif st.status in ("failed", "blocked", "partial", "login_needed", "login_expired"):
            d["failed"] += 1
        if st.status == "split":
            d["split"] += 1
    return {"run": _run_dict(r), "coverage": list(by_site.values()),
            "searches": [st.model_dump() for st in stats], "events": events.recent(run_id)}


class RescoreIn(BaseModel):
    scope: str = "all"


@app.post("/api/runs/rescore")
async def rescore(body: RescoreIn):
    try:
        n = await asyncio.to_thread(runs.rescore_sync, body.scope)
        return {"rescored": n}
    except ValueError as e:
        _err(e)


# --------------------------------------------------------------------- jobs ---
@app.get("/api/jobs")
def list_jobs(tab: str = "all", verdict: Optional[str] = None, min_score: Optional[int] = None,
              max_score: Optional[int] = None, source: Optional[str] = None, role: Optional[str] = None,
              status: Optional[str] = None, q: Optional[str] = None, hide_hard_blockers: bool = False,
              limit: int = 2000):
    with db.session_scope() as s:
        query = select(m.Job)
        if tab == "new":
            query = query.where(m.Job.status.in_(["new", "scored"]))
        elif tab == "built":
            query = query.where(m.Job.status == "built")
        elif tab == "applied":
            query = query.where(m.Job.status.in_(APPLIED_STATES))
        elif tab == "review":
            query = query.where(m.Job.status == "needs_review")
        elif tab == "excluded":
            query = query.where(m.Job.status == "excluded")
        elif tab in ("all", "email"):
            query = query.where(m.Job.status != "excluded")
        if status:
            query = query.where(m.Job.status == status)
        if role:
            query = query.where(m.Job.role_category == role)
        if q:
            like = f"%{q}%"
            query = query.where(or_(m.Job.title.like(like), m.Job.company.like(like), m.Job.location.like(like)))
        jobs = s.exec(query.order_by(m.Job.id.desc()).limit(limit)).all()
        scores = {sc.job_id: sc for sc in s.exec(select(m.Score)).all()}
        kinds: dict[int, set] = {}
        for jid_, kind in s.exec(select(m.Document.job_id, m.Document.kind)).all():
            kinds.setdefault(jid_, set()).add(kind)
    out = []
    for j in jobs:
        sc = scores.get(j.id)
        if verdict and (not sc or sc.verdict != verdict):
            continue
        if min_score is not None and (not sc or sc.total < min_score):
            continue
        if max_score is not None and (not sc or sc.total > max_score):
            continue
        if source and source not in j.sources:
            continue
        if tab == "email" and not any(str(x).startswith("email") for x in (j.sources or [])):
            continue
        if hide_hard_blockers and sc and any(d.get("hard") for d in sc.deal_breakers):
            continue
        d = job_dict(j, sc)
        d["has_cv"] = "cv" in kinds.get(j.id, ())
        d["has_letter"] = "letter" in kinds.get(j.id, ())
        out.append(d)
    out.sort(key=lambda d: -(d["score"] if d["score"] is not None else -1))
    return out


APPLIED_STATES = ["applied", "interview", "offer", "rejected"]


@app.get("/api/sites/{site}/mode")
def site_mode(site: str):
    from ..graphs.search_graph import _get_start_mode
    return {"site": site, "mode": _get_start_mode(site)}


class ModeIn(BaseModel):
    mode: str


@app.put("/api/sites/{site}/mode")
def set_site_mode(site: str, body: ModeIn):
    from ..graphs.search_graph import _set_start_mode
    if body.mode not in ("guest", "window", "manual"):
        raise HTTPException(400, "bad mode")
    _set_start_mode(site, body.mode)
    return {"site": site, "mode": body.mode}


@app.get("/api/sites/{site}/search-links")
def site_search_links(site: str, filter_profile_id: Optional[int] = None):
    """The searches the app would run on a site, as links to open in your own browser."""
    from ..agents.sites import SITES
    from ..steps import planner
    if site not in SITES:
        raise HTTPException(404, "unknown site")
    with db.session_scope() as s:
        fp = s.get(m.FilterProfile, filter_profile_id) if filter_profile_id else None
        fp = fp or s.exec(select(m.FilterProfile).where(m.FilterProfile.is_default == True)).first()  # noqa: E712
        fp = fp or s.exec(select(m.FilterProfile)).first()
        if not fp:
            return []
        qs = planner.plan_site_queries(site, fp.roles, fp.job_types, fp.locations, fp.days, fp.work_modes, fp.keywords_in)
    spec = SITES[site]
    out = []
    for q in qs:
        terms = q.any_terms or [q.keywords]
        label = f"{q.location}: {', '.join(q.type_words) + ' – ' if q.type_words else ''}{len(terms)} role term{'s' if len(terms) > 1 else ''}" \
            if q.any_terms else f"{q.keywords} – {q.location}"
        out.append({"label": label, "location": q.location, "url": spec.search_url(spec, q, 0, "guest")})
    return out


# ------------------------------------------------------------- apply assistant ---
@app.get("/api/apply/profile")
def apply_profile():
    from ..apply import profile as AP
    p = AP.load()
    return {"profile": p.model_dump(), "missing": AP.missing_important(p)}


@app.put("/api/apply/profile")
def apply_profile_save(body: dict):
    from ..apply import profile as AP
    try:
        p = AP.ApplicantProfile.model_validate(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    AP.save(p)
    return {"profile": p.model_dump(), "missing": AP.missing_important(p)}


@app.get("/api/apply/answers")
def apply_answers():
    with db.session_scope() as s:
        rows = s.exec(select(m.SavedAnswer).order_by(m.SavedAnswer.updated_at.desc())).all()
        return [r.model_dump(mode="json") for r in rows]


class AnswerIn(BaseModel):
    question: str = ""
    answer: str


@app.post("/api/apply/answers")
def apply_answer_add(body: AnswerIn):
    from ..apply import profile as AP
    AP.remember(body.question, body.answer, "you")
    return {"ok": True}


@app.put("/api/apply/answers/{aid}")
def apply_answer_edit(aid: int, body: AnswerIn):
    with db.session_scope() as s:
        r = s.get(m.SavedAnswer, aid)
        if not r:
            raise HTTPException(404)
        r.answer, r.source, r.updated_at = body.answer, "you", datetime.now().astimezone()
        s.add(r)
    return {"ok": True}


@app.delete("/api/apply/answers/{aid}")
def apply_answer_delete(aid: int):
    with db.session_scope() as s:
        r = s.get(m.SavedAnswer, aid)
        if r:
            s.delete(r)
    return {"ok": True}


@app.get("/api/apply/queue")
def apply_queue(limit: Optional[int] = None, all: bool = False):
    from ..apply import session as AS
    return {"limit": settings.apply_daily_limit, "jobs": AS.queue(limit, include_all=all)}


@app.post("/api/apply/jobs/{job_id}/not-applied")
def apply_not_applied(job_id: int):
    """Undo a wrong 'Applied' (you did not finish or it failed): the job is back in your queue."""
    from ..apply import bridge, session as AS
    if (bridge.active().get("job") or {}).get("id") == job_id:
        bridge.set_active(None)
    try:
        AS.mark_not_applied(job_id)
    except KeyError:
        raise HTTPException(404, "job not found")
    return {"ok": True}


@app.post("/api/apply/jobs/{job_id}/applied")
def apply_mark_applied(job_id: int):
    from ..apply import session as AS
    AS.mark_applied_by_hand(job_id)
    return {"ok": True}


class ApplyStartIn(BaseModel):
    job_ids: list[int]


@app.post("/api/apply/start")
async def apply_start(body: ApplyStartIn):
    from ..apply import session as AS
    if not body.job_ids:
        raise HTTPException(400, "Pick at least one job")
    try:
        AS.SESSION.start(body.job_ids[: max(1, settings.apply_daily_limit)])
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return AS.SESSION.snapshot()


class ApplyControlIn(BaseModel):
    action: str


@app.post("/api/apply/control")
def apply_control(body: ApplyControlIn):
    from ..apply import session as AS
    try:
        AS.SESSION.control(body.action)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return AS.SESSION.snapshot()


@app.get("/api/apply/state")
def apply_state():
    from ..apply import session as AS
    return AS.SESSION.snapshot()


# --- browser extension (apply in your own Chrome / Edge)
class ActiveIn(BaseModel):
    job_id: Optional[int] = None


EXT_SEEN: dict = {"at": None}


@app.get("/api/ext/ping")
def ext_ping(source: str = ""):
    if source == "ext":
        EXT_SEEN["at"] = datetime.now().astimezone().isoformat()
    return {"ok": True, "app": "job-hunt-agent", "version": 1}


@app.get("/api/ext/status")
def ext_status():
    return {"seen_at": EXT_SEEN["at"], "folder": str(PROJECT_ROOT / "extension")}


@app.get("/api/ext/active")
def ext_active():
    from ..apply import bridge
    return bridge.active()


@app.get("/api/ext/match")
def ext_match(url: str = "", title: str = ""):
    from ..apply import bridge
    return {"candidates": bridge.match(url, title)}


@app.post("/api/ext/active")
def ext_set_active(body: ActiveIn):
    from ..apply import bridge
    return bridge.set_active(body.job_id)


class DecideIn(BaseModel):
    job_id: Optional[int] = None
    url: str = ""
    fields: list[dict] = []


@app.post("/api/ext/decide")
async def ext_decide(body: DecideIn):
    from ..apply import bridge
    jid = body.job_id or (bridge.active().get("job") or {}).get("id")
    if not jid:
        raise HTTPException(409, "No job selected - pick one on the app's Apply page first")
    try:
        decisions = await asyncio.to_thread(bridge.decide_fields, jid, body.fields)
    except KeyError:
        raise HTTPException(404, "job not found")
    return {"job_id": jid, "decisions": decisions}


@app.get("/api/ext/file/{job_id}/{kind}")
def ext_file(job_id: int, kind: str):
    from ..apply import bridge
    try:
        return bridge.file_b64(job_id, kind)
    except KeyError:
        raise HTTPException(404, f"no {kind} file for this job")


class DoneIn(BaseModel):
    job_id: Optional[int] = None
    url: str = ""
    screenshot: str = ""
    answers: list[dict] = []
    how: str = ""


@app.post("/api/ext/learn")
def ext_learn(body: DoneIn):
    from ..apply import bridge
    return {"learned": bridge.learn(body.answers)}


@app.post("/api/ext/done")
def ext_done(body: DoneIn):
    from ..apply import bridge
    jid = body.job_id or (bridge.active().get("job") or {}).get("id")
    if not jid:
        raise HTTPException(409, "No job selected")
    return bridge.done(jid, body.url, body.screenshot, body.answers, body.how)


@app.get("/api/apply/history")
def apply_history(limit: int = 100):
    with db.session_scope() as s:
        rows = s.exec(select(m.ApplyAttempt).order_by(m.ApplyAttempt.id.desc()).limit(limit)).all()
        jobs = {j.id: j for j in s.exec(select(m.Job).where(m.Job.id.in_([r.job_id for r in rows] or [0]))).all()}
        from ..apply.session import _host
        from ..apply import bridge, session as AS
        bridge.active()
        act = bridge.ACTIVE.get("attempt_id")
        out = []
        for r in rows:
            j = jobs.get(r.job_id)
            d = r.model_dump(mode="json")
            if d["status"] == "running" and ((r.note == "in your browser" and r.id != act)
                                             or (r.note != "in your browser" and not AS.SESSION.running)):
                d["status"], d["note"] = "skipped", "not finished"
            out.append({**d, "company": (j.company or _host(j.link)) if j else "", "title": j.title if j else "",
                        "link": j.link if j else "", "job_status": j.status if j else ""})
        return out


@app.get("/api/websearch/status")
def websearch_status():
    from ..feeds import websearch as W
    eng = W.engine()
    return {"enabled": settings.websearch_enabled, "engine": eng or None, "claude_cli": W.claude_cli() or None,
            "has_api_key": bool(settings.anthropic_api_key), "max_calls": settings.websearch_max_calls,
            "hint": None if eng else "Install Claude Code (npm install -g @anthropic-ai/claude-code), run `claude` once to "
                                      "log in with your Claude account, then restart the app. Or add an API key in .env."}


@app.get("/api/websearch/test")
async def websearch_test(location: str = "Berlin", group: int = 1):
    """One real web search (one place, one role group) - nothing is saved."""
    from ..feeds import websearch as W
    g, terms = W.ROLE_GROUPS[max(0, min(group, len(W.ROLE_GROUPS) - 1))]
    q = W.WebQuery(site="web", location=location, group=g, terms=terms,
                   type_words=["Werkstudent", "Praktikum", "Working Student", "Intern"], days=7)
    try:
        r = await W.run_query(q)
    except W.WebSearchUnavailable as e:
        return {"ok": False, "error": str(e)}
    return {"ok": not r.error, "engine": W.engine(), "searches": r.searches, "links_seen": r.seen_links,
            "kept": len(r.jobs), "dropped": r.dropped, "error": r.error, "note": r.note,
            "jobs": [{"title": j.title, "company": j.company, "location": j.location, "source": j.source, "url": j.url}
                     for j in r.jobs]}


@app.get("/api/diagnose/{site}")
async def diagnose_site(site: str, keywords: str = "", location: str = "Berlin", pages: int = 1):
    """Run ONE search on one site the way a real run does (window, checks, pacing) and report what happened.
    Nothing is saved to the job board. Blocked pages land in data/debug/<site>/."""
    from ..agents.site_agent import SiteAgent
    from ..agents.sites import SITES
    from ..graphs.search_graph import _get_start_mode
    from ..schemas import Query
    from ..steps import planner
    if site not in SITES:
        raise HTTPException(404, "unknown site")
    if keywords:
        qy = Query(site=site, keywords=keywords, location=location, days=5)
    else:
        qy = planner.plan_site_queries(site, list(planner.ROLE_TERMS), ["Internship", "Working Student", "Part-time"],
                                       [location], 5)[0]
    log_lines: list[str] = []

    async def on_captcha(site_name: str, url: str) -> bool:
        events.emit({"type": "captcha", "site": site_name, "url": url, "message":
                     f"{SITES[site_name].label} wants a 'verify you are human' check (live check) - solve it in the browser window"})
        return await events.wait_for(f"captcha:diag:{site_name}", timeout=180)

    agent = SiteAgent(site, connection_status=connections.get_status(site), on_captcha=on_captcha,
                      emit=lambda e: log_lines.append(e.get("message", "")), max_pages=max(1, min(pages, 5)),
                      start_mode=_get_start_mode(site))
    try:
        out, jobs = await agent.run_query(qy)
    finally:
        await agent.close()
    return {"site": site, "query": qy.keywords, "location": qy.location, "url": SITES[site].search_url(SITES[site], qy, 0, out.mode),
            "status": out.status, "mode": out.mode, "reported": out.reported, "collected": out.collected,
            "pages": out.pages, "error": out.error, "log": log_lines,
            "jobs": [{"title": j.title, "company": j.company, "location": j.location} for j in jobs[:15]]}


@app.get("/api/jobs/counts")
def job_counts():
    """Number of jobs on each job-board tab."""
    with db.session_scope() as s:
        by = dict(s.exec(select(m.Job.status, func.count()).group_by(m.Job.status)).all())
        email = sum(1 for st, src in s.exec(select(m.Job.status, m.Job.sources).where(m.Job.status != "excluded")).all()
                    if any(str(x).startswith("email") for x in (src or [])))
    g = lambda *ks: sum(by.get(k, 0) for k in ks)
    return {"new": g("new", "scored"), "built": g("built"), "applied": g(*APPLIED_STATES),
            "review": g("needs_review"), "excluded": g("excluded"), "email": email,
            "all": sum(v for k, v in by.items() if k != "excluded")}


@app.get("/api/jobs/deleted")
def list_deleted():
    with db.session_scope() as s:
        return [d.model_dump(mode="json") for d in s.exec(select(m.DeletedJob).order_by(m.DeletedJob.id.desc())).all()]


@app.get("/api/jobs/{jid}")
def get_job(jid: int):
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        if not j:
            raise HTTPException(404)
        sc = s.exec(select(m.Score).where(m.Score.job_id == jid)).first()
        docs = s.exec(select(m.Document).where(m.Document.job_id == jid)).all()
        app_row = s.exec(select(m.Application).where(m.Application.job_id == jid)).first()
    d = job_dict(j, sc, full=True)
    d["documents"] = [doc_dict(x) for x in docs]
    d["application"] = app_row.model_dump(mode="json") if app_row else None
    return d


class JobPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    job_type: Optional[str] = None


@app.patch("/api/jobs/{jid}")
def patch_job(jid: int, body: JobPatch):
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        if not j:
            raise HTTPException(404)
        if body.status:
            if body.status not in m.JobStatus.ALL:
                raise HTTPException(400, "bad status")
            j.status = body.status
            if body.status != "excluded":
                j.exclude_reason = ""
        if body.notes is not None:
            j.notes = body.notes
        if body.job_type:
            j.job_type = body.job_type
            if j.status == "needs_review":
                has_score = s.exec(select(m.Score).where(m.Score.job_id == jid)).first()
                j.status, j.exclude_reason = ("scored" if has_score else "new"), ""
        j.updated_at = datetime.now().astimezone()
        s.add(j)
        if body.status == "applied":
            a = s.exec(select(m.Application).where(m.Application.job_id == jid)).first() or m.Application(job_id=jid)
            a.applied_at = a.applied_at or date.today()
            a.follow_up_at = a.follow_up_at or date.today() + timedelta(days=12)
            s.add(a)
    return get_job(jid)


class DeleteIn(BaseModel):
    job_ids: list[int]
    delete_files: bool = False
    forget: bool = False      # True = allow it to be found again by later searches


@app.post("/api/jobs/delete")
def delete_jobs_endpoint(body: DeleteIn):
    from ..steps.clean import delete_jobs
    return delete_jobs(body.job_ids, body.delete_files, body.forget)


@app.delete("/api/jobs/{jid}")
def delete_job(jid: int, delete_files: bool = False, forget: bool = False):
    from ..steps.clean import delete_jobs
    r = delete_jobs([jid], delete_files, forget)
    if not r["deleted"]:
        raise HTTPException(404)
    return r


@app.delete("/api/jobs/deleted/{did}")
def undelete(did: int):
    """Forget a deletion, so the job can be found again by the next search."""
    with db.session_scope() as s:
        d = s.get(m.DeletedJob, did)
        if d:
            s.delete(d)
    return {"ok": True}


# -------------------------------------------------------------------- build ---
@app.get("/api/build/candidates")
def get_candidates(min_score: Optional[int] = None):
    return build_candidates(min_score)


class BuildIn(BaseModel):
    job_ids: list[int] = []
    cv_profile_id: Optional[int] = None
    rebuild: bool = False


@app.post("/api/build")
async def start_build(body: BuildIn):
    if not cvs.get_main() and not body.cv_profile_id:
        _err(ValueError("Upload your main CV first (My CV screen)."))
    return {"run_id": await runs.start_build(body.job_ids or None, body.cv_profile_id, body.rebuild)}


class ResumeBuildIn(BaseModel):
    job_ids: list[int]


@app.post("/api/build/{run_id}/resume")
async def resume_build(run_id: int, body: ResumeBuildIn):
    return {"run_id": await runs.resume_build(run_id, body.job_ids)}


# ---------------------------------------------------------------- documents ---
def doc_dict(d: m.Document) -> dict:
    return {"id": d.id, "job_id": d.job_id, "kind": d.kind, "docx_path": d.docx_path, "pdf_path": d.pdf_path,
            "qa_results": d.qa_results, "qa_passed": d.qa_passed, "blockers_stated": d.blockers_stated,
            "attempts": d.attempts, "built_at": d.built_at.isoformat(), "cv_version_id": d.cv_version_id,
            "outdated": bool((d.content or {}).get("_outdated")),
            "imported": bool((d.content or {}).get("_imported")),
            "content": {k: v for k, v in (d.content or {}).items() if not k.startswith("_")}}


@app.get("/api/documents")
def list_documents(job_id: Optional[int] = None):
    from ..sync_docs import sync_all
    sync_all()
    with db.session_scope() as s:
        q = select(m.Document)
        if job_id:
            q = q.where(m.Document.job_id == job_id)
        docs = s.exec(q.order_by(m.Document.id.desc())).all()
        jobs = {j.id: j for j in s.exec(select(m.Job)).all()}
    out = []
    for d in docs:
        x = doc_dict(d)
        j = jobs.get(d.job_id)
        x["company"], x["title"] = (j.company, j.title) if j else ("", "")
        out.append(x)
    return out


class ContentIn(BaseModel):
    content: dict


@app.put("/api/documents/{doc_id}")
def update_document(doc_id: int, body: ContentIn):
    """Quick edit: save your changes, re-render the same files and re-run the checks."""
    main = cvs.get_main()
    if not main:
        _err(ValueError("No main CV"))
    prof, _ = main
    style = cvs.active_style()
    with db.session_scope() as s:
        d = s.get(m.Document, doc_id)
        if not d:
            raise HTTPException(404)
        job = s.get(m.Job, d.job_id)
        docx_path = Path(d.docx_path)
        if d.kind == "cv":
            cv = CVContent.model_validate(body.content)
            render_cv(cv, prof, style, docx_path)
            pdf = to_pdf(docx_path)
            checks = qa.check_cv(cv, prof, docx_path, pdf, style)
        else:
            L = LetterContent.model_validate(body.content)
            cv_doc = s.exec(select(m.Document).where(m.Document.job_id == d.job_id, m.Document.kind == "cv")).first()
            tagline = (cv_doc.content or {}).get("tagline", prof.tagline) if cv_doc else prof.tagline
            render_letter(L, prof, tagline, style, docx_path, company=job.company)
            pdf = to_pdf(docx_path)
            checks = qa.check_letter(L, prof, pdf, style)
            d.blockers_stated = L.blockers_stated
        d.content = body.content
        d.pdf_path = str(pdf)
        d.qa_results = [c.as_dict() for c in checks]
        d.qa_passed = qa.passed(checks)
        d.built_at = datetime.now().astimezone()
        s.add(d)
        s.flush()
        return doc_dict(d)


@app.delete("/api/documents/job/{job_id}")
def delete_job_documents(job_id: int, delete_files: bool = True):
    """Delete the tailored CV + cover letter of one job (the job itself stays on the board)."""
    removed = 0
    with db.session_scope() as s:
        docs = s.exec(select(m.Document).where(m.Document.job_id == job_id)).all()
        if not docs:
            raise HTTPException(404, "No documents for this job")
        folders = set()
        for d in docs:
            if delete_files:
                for f in (d.docx_path, d.pdf_path):
                    if f and Path(f).exists():
                        try:
                            Path(f).unlink()
                            removed += 1
                            folders.add(Path(f).parent)
                        except OSError:
                            pass
            s.delete(d)
        for folder in folders:
            try:
                if folder.exists() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                pass
        j = s.get(m.Job, job_id)
        if j and j.status == m.JobStatus.BUILT:
            j.status = m.JobStatus.SCORED
            s.add(j)
    return {"ok": True, "files_deleted": removed}


@app.get("/api/files")
def get_file(path: str, download: bool = False):
    p = _safe_path(path)
    media = {".pdf": "application/pdf",
             ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(p.suffix.lower())
    headers = {} if not download else {"Content-Disposition": f'attachment; filename="{p.name}"'}
    if not download:
        return FileResponse(p, media_type=media, headers={"Content-Disposition": "inline"})
    return FileResponse(p, media_type=media, filename=p.name)


@app.get("/api/files/pages")
def pdf_pages(path: str):
    p = _safe_path(path)
    return {"pages": qa.page_count(p) if p.suffix.lower() == ".pdf" else 0}


@app.get("/api/files/page-image")
def pdf_page_image(path: str, page: int = 0, scale: float = 1.6):
    """A PDF page as PNG, so previews work in every browser (no PDF plugin needed)."""
    p = _safe_path(path)
    try:
        png = qa.render_page_png(p, page, max(0.5, min(scale, 3.0)), Path(settings.data_dir) / "page_cache")
    except IndexError:
        raise HTTPException(404, "page out of range")
    return FileResponse(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/files/open-folder")
def open_folder(body: dict):
    """Open a folder in Windows Explorer (local app only)."""
    import os
    import sys
    p = _safe_path(body.get("path", ""))
    target = p if p.is_dir() else p.parent
    if sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
        return {"ok": True}
    return {"ok": False, "path": str(target)}


# ------------------------------------------------------------------ tracker ---
class AppIn(BaseModel):
    applied_at: Optional[date] = None
    notes: Optional[str] = None
    follow_up_at: Optional[date] = None
    outcome: Optional[str] = None
    status: Optional[str] = None


@app.post("/api/docs/sync")
def docs_sync():
    from ..sync_docs import sync_all
    return sync_all()


@app.get("/api/applications")
def list_applications():
    from ..sync_docs import doc_state, sync_all
    sync_all()
    with db.session_scope() as s:
        all_docs = s.exec(select(m.Document)).all()
        rows = s.exec(select(m.Job).where(m.Job.status.in_(["built", "applied", "interview", "offer", "rejected"]))).all()
        apps = {a.job_id: a for a in s.exec(select(m.Application)).all()}
        scores = {sc.job_id: sc for sc in s.exec(select(m.Score)).all()}
    out = []
    for j in rows:
        a = apps.get(j.id)
        d = job_dict(j, scores.get(j.id))
        d["application"] = a.model_dump(mode="json") if a else None
        d["docs"] = doc_state(j.id, all_docs)
        d["follow_up_due"] = bool(a and a.follow_up_at and a.follow_up_at <= date.today()
                                  and j.status == "applied")
        out.append(d)
    return out


@app.put("/api/applications/{job_id}")
def update_application(job_id: int, body: AppIn):
    with db.session_scope() as s:
        a = s.exec(select(m.Application).where(m.Application.job_id == job_id)).first() or m.Application(job_id=job_id)
        for k in ("applied_at", "notes", "follow_up_at", "outcome"):
            v = getattr(body, k)
            if v is not None:
                setattr(a, k, v)
        if body.status:
            j = s.get(m.Job, job_id)
            j.status = body.status
            s.add(j)
            if body.status == "applied" and not a.applied_at:
                a.applied_at = date.today()
                a.follow_up_at = a.follow_up_at or date.today() + timedelta(days=12)
        s.add(a)
    return {"ok": True}


# -------------------------------------------------------------- connections ---
@app.get("/api/connections")
def get_connections():
    return connections.list_connections()


@app.post("/api/connections/{site}/login")
async def login(site: str):
    connections.start_login(site)
    return {"ok": True, "message": "A browser window opened. Log in, then close the window."}


@app.post("/api/connections/{site}/verify")
async def verify(site: str):
    try:
        return {"status": await connections.verify(site)}
    except Exception as e:
        _err(e)


@app.delete("/api/connections/{site}")
def disconnect(site: str):
    connections.disconnect(site)
    return {"ok": True}


# ---------------------------------------------------------------- companies ---
class CompanyIn(BaseModel):
    name: str
    ats: str = ""
    board_id: str = ""
    careers_url: str = ""


@app.get("/api/companies")
def list_companies():
    with db.session_scope() as s:
        return [c.model_dump() for c in s.exec(select(m.TargetCompany)).all()]


@app.post("/api/companies")
def add_company(c: CompanyIn):
    with db.session_scope() as s:
        row = m.TargetCompany(**c.model_dump())
        s.add(row)
        s.flush()
        return row.model_dump()


@app.delete("/api/companies/{cid}")
def delete_company(cid: int):
    with db.session_scope() as s:
        row = s.get(m.TargetCompany, cid)
        if row:
            s.delete(row)
    return {"ok": True}


# ---------------------------------------------------------------------- CV ---
@app.get("/api/cv")
def cv_list():
    out = cvs.list_profiles()
    main = cvs.get_main()
    return {"profiles": out, "main": main[0].model_dump() if main else None,
            "main_version_id": main[1] if main else None, "style": cvs.active_style()}


@app.post("/api/cv/upload")
async def cv_upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "cv.docx").suffix
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / (Path(file.filename or f"cv{suffix}").name)
        with open(tmp, "wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            return await asyncio.to_thread(cvs.upload, tmp)
        except cvs.CVError as e:
            _err(e)


class ConfirmIn(BaseModel):
    profile: dict
    source_file: str = ""
    name: str = "Main CV"
    make_main: bool = True
    profile_id: Optional[int] = None
    note: str = "Uploaded"


@app.post("/api/cv/confirm")
def cv_confirm(body: ConfirmIn):
    try:
        return cvs.confirm(body.profile, body.source_file, body.name, body.make_main, body.profile_id, body.note)
    except Exception as e:
        _err(e)


@app.get("/api/cv/{pid}")
def cv_get(pid: int):
    got = cvs.get_profile(pid)
    if not got:
        raise HTTPException(404)
    return {"profile": got[0].model_dump(), "version_id": got[1]}


class SaveIn(BaseModel):
    profile: dict
    note: str = "Edited"


@app.put("/api/cv/{pid}")
def cv_save(pid: int, body: SaveIn):
    try:
        return cvs.save_version(pid, body.profile, body.note)
    except Exception as e:
        _err(e)


@app.post("/api/cv/{pid}/main")
def cv_main(pid: int):
    try:
        return {"outdated_scores": cvs.set_main(pid)}
    except cvs.CVError as e:
        _err(e)


@app.post("/api/cv/{pid}/rename")
def cv_rename(pid: int, body: dict):
    cvs.rename(pid, body.get("name", "CV"))
    return {"ok": True}


@app.delete("/api/cv/{pid}")
def cv_delete(pid: int):
    try:
        cvs.archive(pid)
        return {"ok": True}
    except cvs.CVError as e:
        _err(e)


@app.delete("/api/cv/{pid}/permanent")
def cv_delete_permanent(pid: int):
    try:
        cvs.delete_permanently(pid)
        return {"ok": True}
    except cvs.CVError as e:
        _err(e)


@app.delete("/api/cv/versions/{vid}")
def cv_delete_version(vid: int):
    try:
        cvs.delete_version(vid)
        return {"ok": True}
    except cvs.CVError as e:
        _err(e)


@app.post("/api/cv/{pid}/restore")
def cv_restore(pid: int):
    cvs.restore(pid)
    return {"ok": True}


@app.get("/api/cv/versions/{vid}")
def cv_version(vid: int):
    p = cvs.get_version(vid)
    if not p:
        raise HTTPException(404)
    return p.model_dump()


@app.post("/api/cv/versions/{vid}/restore")
def cv_restore_version(vid: int):
    return cvs.restore_version(vid)


@app.get("/api/cv/compare/{a}/{b}")
def cv_compare(a: int, b: int):
    return cvs.compare(a, b)


class PreviewIn(BaseModel):
    profile: dict


@app.post("/api/cv/preview")
async def cv_preview(body: PreviewIn):
    """Render the CV being edited in your template; returns the PDF path and page count."""
    prof = CVProfile.model_validate(body.profile)
    out = Path(settings.data_dir) / "preview"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S%f")
    for old in out.glob("preview_*"):
        old.unlink(missing_ok=True)
    d = render_cv(profile_to_content(prof), prof, cvs.active_style(), out / f"preview_{stamp}.docx")
    try:
        pdf = await asyncio.to_thread(to_pdf, d)
        return {"pdf_path": str(pdf), "docx_path": str(d), "pages": qa.page_count(pdf)}
    except PdfUnavailable as e:
        return {"pdf_path": None, "docx_path": str(d), "pages": None, "error": str(e)}


class SourceIn(BaseModel):
    source_file: str


@app.post("/api/cv/source-pdf")
async def cv_source_pdf(body: SourceIn):
    """PDF of the uploaded original, to show next to the extracted data for review."""
    src = _safe_path(body.source_file)
    if src.suffix.lower() == ".pdf":
        return {"pdf_path": str(src)}
    out = Path(settings.data_dir) / "preview"
    out.mkdir(parents=True, exist_ok=True)
    copy = out / f"source_{src.stem}{src.suffix}"
    shutil.copy2(src, copy)
    try:
        return {"pdf_path": str(await asyncio.to_thread(to_pdf, copy))}
    except PdfUnavailable as e:
        return {"pdf_path": None, "error": str(e)}


@app.post("/api/cv/template")
async def cv_template(file: UploadFile = File(...)):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / Path(file.filename or "template.docx").name
        with open(tmp, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return {"style": cvs.set_template(tmp, name=tmp.name)}


# ------------------------------------------------------------------ settings ---
# ------------------------------------------------------------------- your Gmail ---
class MailSettingsIn(BaseModel):
    address: Optional[str] = None
    app_password: Optional[str] = None
    enabled: Optional[bool] = None
    interval_min: Optional[int] = None
    first_days: Optional[int] = None
    add_jobs: Optional[bool] = None
    min_score: Optional[int] = None


@app.get("/api/mail/status")
def mail_status():
    from ..mail import service as mail
    return mail.public()


@app.put("/api/mail/settings")
async def mail_settings(body: MailSettingsIn):
    from ..mail import reader, service as mail
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if patch.get("app_password") or patch.get("address"):
        cfg = mail.config()
        addr = (patch.get("address") or cfg["address"]).strip()
        pw = (patch.get("app_password") or cfg["app_password"]).replace(" ", "")
        if not (addr and pw):
            raise HTTPException(400, "Enter your Gmail address and the app password")
        try:
            await asyncio.to_thread(reader.test_login, addr, pw)
        except reader.AuthError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(400, f"Could not reach Gmail: {e}")
        patch.update(address=addr, app_password=pw)
        patch.setdefault("enabled", True)
    old_min = mail.config().get("min_score", 60)
    mail.save_config(patch)
    scheduler.reschedule_mail()
    if "min_score" in patch and patch["min_score"] != old_min:
        await asyncio.to_thread(mail.recheck_email_jobs, patch["min_score"])
    return mail.public()


@app.post("/api/mail/disconnect")
def mail_disconnect():
    from ..mail import service as mail
    mail.save_config({"enabled": False})
    cfg = mail.config()
    cfg["app_password"] = ""
    with db.session_scope() as s:
        row = s.get(m.AppSetting, mail.KEY)
        row.value = json.dumps(cfg, default=str)
        s.add(row)
    scheduler.reschedule_mail()
    return mail.public()


@app.post("/api/mail/check")
async def mail_check():
    from ..mail import service as mail
    if mail.STATE["running"]:
        return {"started": False, "running": True}
    asyncio.get_event_loop().create_task(asyncio.to_thread(mail.check_now))
    return {"started": True}


@app.get("/api/mail/events")
def mail_events(kind: str = "", limit: int = 300):
    with db.session_scope() as s:
        q = select(m.MailEvent).order_by(m.MailEvent.received_at.desc())
        if kind == "todo":
            q = q.where(m.MailEvent.kind.in_(["interview", "assessment", "offer"]), m.MailEvent.done == False)  # noqa: E712
        elif kind:
            q = q.where(m.MailEvent.kind.in_(kind.split(",")))
        rows = s.exec(q.limit(limit)).all()
        jobs = {j.id: j for j in s.exec(select(m.Job).where(m.Job.id.in_([r.job_id for r in rows if r.job_id]))).all()}
        found_ids = [i for r in rows for i in (r.job_ids or [])]
        found = {j.id: j for j in s.exec(select(m.Job).where(m.Job.id.in_(found_ids))).all()} if found_ids else {}
        fscores = {sc.job_id: sc.total for sc in s.exec(select(m.Score).where(m.Score.job_id.in_(found_ids))).all()} if found_ids else {}
        out = []
        for r in rows:
            j = jobs.get(r.job_id)
            good = [{"id": i, "title": found[i].title, "company": found[i].company, "score": fscores.get(i),
                     "status": found[i].status} for i in (r.job_ids or []) if i in found and found[i].status != "excluded"]
            out.append({**r.model_dump(mode="json"), "matches": sorted(good, key=lambda x: -(x["score"] or 0)),
                        "job": {"id": j.id, "title": j.title, "company": j.company, "status": j.status} if j else None,
                        "gmail": f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{r.msg_id.strip('<>')}"})
        return out


@app.post("/api/mail/events/{event_id}/done")
def mail_event_done(event_id: int, done: bool = True):
    with db.session_scope() as s:
        ev = s.get(m.MailEvent, event_id)
        if not ev:
            raise HTTPException(404, "not found")
        ev.done = done
        s.add(ev)
    return {"ok": True}


class LinkIn(BaseModel):
    job_id: Optional[int] = None


@app.post("/api/mail/events/{event_id}/link")
def mail_event_link(event_id: int, body: LinkIn):
    from ..mail import service as mail
    try:
        return mail.link_event(event_id, body.job_id)
    except KeyError:
        raise HTTPException(404, "not found")


@app.get("/api/settings")
def get_settings():
    return settings.public_dict()


@app.put("/api/settings")
def put_settings(body: dict):
    db.save_settings(body)
    scheduler.reschedule()
    return settings.public_dict()


@app.post("/api/import/previous")
async def import_previous():
    from ..importer import import_previous as run_import
    return await asyncio.to_thread(run_import)


# ------------------------------------------------------------- export/insights ---
@app.get("/api/export/jobs.xlsx")
def export_jobs_xlsx():
    path = Path(settings.data_dir) / f"All_Jobs_{date.today().isoformat()}.xlsx"
    export_all(path)
    return FileResponse(path, filename=path.name)


@app.get("/api/insights/missing-keywords")
def insights(n: int = 15):
    return [{"keyword": k, "jobs": c} for k, c in top_missing_keywords(n)]


# ----------------------------------------------------------------- websocket ---
@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    q = events.subscribe()
    try:
        await ws.send_json({"type": "hello", "recent": events.recent(limit=50)})
        while True:
            ev = await q.get()
            await ws.send_json(ev)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        events.unsubscribe(q)


# ------------------------------------------------------------------ frontend ---
DIST = PROJECT_ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        f = DIST / full_path
        if full_path and f.exists() and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
