"""The apply assistant: works through your queue one job at a time while you watch.

For every job it opens the posting in its own Chrome window (logins you do there are remembered),
goes to the application form, fills everything it honestly can, and waits. You read it, answer
what is red, tick the consent boxes and press Submit. When the page says the application was
received, the job is marked "Applied", your new answers are saved for next time, and it moves on.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlmodel import select

from .. import db, events, models as m
from ..config import settings
from . import filler, profile as P
from .draft import make_batch_answerer, make_drafter
from .fields import Context

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0
JOB_TIMEOUT_MINUTES = 30
PAGE_FACTORY = None        # test hook: async () -> (context, page)


def _now():
    return datetime.now().astimezone()


def cv_text() -> str:
    from ..cv.service import get_main
    got = get_main()
    if not got:
        return ""
    cv, _ = got
    parts = [cv.summary]
    for g in cv.skills:
        parts.append(f"{g.category}: " + ", ".join(g.items))
    for e in cv.experience:
        parts += [f"{e.title} {e.org}"] + list(e.bullets)
    for p in cv.projects:
        parts += [p.name] + list(p.bullets)
    for e in cv.education:
        parts += [f"{e.degree} {e.school}"] + list(e.bullets)
    for c in cv.certifications:
        parts.append(c.name)
    return "\n".join(x for x in parts if x)


def _host(link: str) -> str:
    from urllib.parse import urlparse
    h = urlparse(link or "").netloc.lower()
    return h[4:] if h.startswith("www.") else h


def queue(limit: Optional[int] = None, include_all: bool = False) -> list[dict]:
    """Jobs with a finished CV. Default: not applied yet, best score first, max the daily limit.
    include_all: every built AND applied job (applied ones last), with the last apply attempt."""
    limit = limit or settings.apply_daily_limit
    with db.session_scope() as s:
        docs: dict[int, dict] = {}
        for d in s.exec(select(m.Document)).all():
            path = d.pdf_path if d.pdf_path and Path(d.pdf_path).exists() else ""   # forms get the PDF only
            if path:
                docs.setdefault(d.job_id, {})[d.kind] = path
        last: dict[int, m.ApplyAttempt] = {}
        for a in s.exec(select(m.ApplyAttempt).order_by(m.ApplyAttempt.id)).all():
            last[a.job_id] = a
        states = [m.JobStatus.BUILT, m.JobStatus.APPLIED] if include_all else [m.JobStatus.BUILT]
        jobs = s.exec(select(m.Job).where(m.Job.status.in_(states))).all()
        scores = {sc.job_id: sc for sc in s.exec(select(m.Score)).all()}
        apps = {a.job_id: a for a in s.exec(select(m.Application)).all()}
        out = []
        for j in jobs:
            d = docs.get(j.id, {})
            if "cv" not in d or not j.link:
                continue
            sc, la, ap = scores.get(j.id), last.get(j.id), apps.get(j.id)
            out.append({"id": j.id, "title": j.title, "company": j.company or _host(j.link), "location": j.location,
                        "job_type": j.job_type, "link": j.link, "score": sc.total if sc else None,
                        "verdict": sc.verdict if sc else None, "cv": d.get("cv"), "letter": d.get("letter"),
                        "applied": j.status == m.JobStatus.APPLIED,
                        "applied_at": ap.applied_at.isoformat() if ap and ap.applied_at else None,
                        "last_attempt": {"status": la.status, "note": la.note, "at": la.started_at.isoformat() if la.started_at else None,
                                         "screenshot": la.screenshot} if la else None})
    out.sort(key=lambda x: (x["applied"], -(x["score"] or 0)))
    return out if include_all else out[:limit]


def mark_not_applied(job_id: int) -> None:
    """Undo: the job was marked Applied but you did not really send it."""
    with db.session_scope() as s:
        job = s.get(m.Job, job_id)
        if not job:
            raise KeyError(job_id)
        if job.status == m.JobStatus.APPLIED:
            job.status = m.JobStatus.BUILT
            s.add(job)
        a = s.exec(select(m.Application).where(m.Application.job_id == job_id)).first()
        if a:
            a.applied_at = None
            a.notes = ((a.notes + "\n") if a.notes else "") + f"{date.today().isoformat()}: marked as NOT applied (corrected by you)"
            s.add(a)
        for att in s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.job_id == job_id,
                                                      m.ApplyAttempt.status.in_(["applied", "running"]))).all():
            att.status, att.note = "skipped", "not applied - corrected by you"
            att.finished_at = att.finished_at or _now()
            s.add(att)
    events.emit({"type": "apply", "message": "marked as not applied", "result": "undo", "job_id": job_id})


def mark_applied_by_hand(job_id: int) -> None:
    _mark_applied(job_id, "Marked as applied by you")
    with db.session_scope() as s:
        att = s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.job_id == job_id, m.ApplyAttempt.status == "running")
                     .order_by(m.ApplyAttempt.id.desc())).first() or m.ApplyAttempt(job_id=job_id, note="marked by you")
        att.status, att.finished_at = "applied", _now()
        s.add(att)
    events.emit({"type": "apply", "message": "marked as applied", "result": "applied", "job_id": job_id})


class ApplySession:
    def __init__(self):
        self.task: Optional[asyncio.Task] = None
        self.job_ids: list[int] = []
        self.index = -1
        self.state = "idle"                  # idle / opening / filling / waiting / done / stopped
        self.current: dict = {}
        self.report: dict = {}
        self.history: list[dict] = []
        self._action: Optional[str] = None   # next / skip / stop  (from the app's buttons)
        self._ctx = None
        self._page = None

    # ------------------------------------------------------------ control ---
    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def start(self, job_ids: list[int]) -> None:
        if self.running:
            raise RuntimeError("The apply assistant is already running")
        self.job_ids, self.index, self.history, self._action = list(job_ids), -1, [], None
        self.task = asyncio.get_event_loop().create_task(self._run())

    def control(self, action: str) -> None:
        if action not in ("next", "skip", "stop"):
            raise ValueError("unknown action")
        self._action = action

    def snapshot(self) -> dict:
        return {"running": self.running, "state": self.state, "index": self.index, "total": len(self.job_ids),
                "current": self.current, "report": self.report, "history": self.history}

    def _emit(self, msg: str, **kw) -> None:
        events.emit({"type": "apply", "message": msg, "state": self.state, "index": self.index,
                     "total": len(self.job_ids), **kw})

    # ---------------------------------------------------------------- run ---
    async def _open_browser(self):
        if PAGE_FACTORY:
            return await PAGE_FACTORY()
        from playwright.async_api import async_playwright
        from ..browser.reader import launch_profile
        self._pw = await async_playwright().start()
        pdir = settings.profiles_dir / "apply"
        pdir.mkdir(parents=True, exist_ok=True)
        try:
            ctx = await self._pw.chromium.launch_persistent_context(str(pdir), channel="chrome", headless=False,
                                                                     viewport=None, args=["--start-maximized"])
        except Exception:
            ctx = await launch_profile(self._pw, pdir, "apply", headless=False, viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return ctx, page

    async def _run(self):
        try:
            self._ctx, self._page = await self._open_browser()
            for self.index, jid in enumerate(self.job_ids):
                if self._action == "stop":
                    break
                self._action = None
                await self._one(jid)
            self.state = "done" if self._action != "stop" else "stopped"
            self._emit(f"Apply session finished: {sum(h['status'] == 'applied' for h in self.history)} applied, "
                       f"{sum(h['status'] == 'skipped' for h in self.history)} skipped")
        except Exception as e:
            log.exception("apply session failed")
            self.state = "stopped"
            self._emit(f"Apply assistant stopped: {e}", level="warn")
        finally:
            try:
                if self._ctx and not PAGE_FACTORY:
                    await self._ctx.close()
                if getattr(self, "_pw", None):
                    await self._pw.stop()
            except Exception:
                pass

    def _context_for(self, job: m.Job, docs: dict) -> Context:
        prof = P.load()
        ctx = Context(profile=prof, job_type=job.job_type, cv_pdf=docs.get("cv", ""), letter_pdf=docs.get("letter", ""),
                      cv_text=cv_text(), company=job.company, title=job.title, job_text=job.full_description or "")
        from .bridge import letter_text
        ctx.letter_text = letter_text(job.id)
        ctx.answerer = make_batch_answerer(ctx)
        ctx.drafter = None if ctx.answerer else make_drafter(ctx)
        return ctx

    async def _one(self, jid: int):
        with db.session_scope() as s:
            job = s.get(m.Job, jid)
            if not job:
                return
            s.expunge(job)
            docs = {}
            for d in s.exec(select(m.Document).where(m.Document.job_id == jid)).all():
                path = d.pdf_path if d.pdf_path and Path(d.pdf_path).exists() else ""   # forms get the PDF only
                if path:
                    docs[d.kind] = path
            att = m.ApplyAttempt(job_id=jid)
            s.add(att)
            s.flush()
            att_id = att.id
        self.current = {"id": jid, "title": job.title, "company": job.company, "link": job.link}
        self.report = {}
        ctx = self._context_for(job, docs)
        page = self._page
        self.state = "opening"
        self._emit(f"Opening {job.company} – {job.title}")
        await page.goto(job.link, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        page = await self._to_form(page)
        self.state = "filling"
        done_fids: set = set()
        rep, waiting = await filler.fill_page(page, ctx, done_fids)
        self.report = rep.as_dict()
        self.report["form_url"] = page.url
        self.state = "waiting"
        self._emit(f"{job.company}: filled {len(rep.filled) + len(rep.uploaded)} fields, "
                   f"{len(rep.open)} for you, {len(rep.drafted)} drafts to read", report=self.report)
        await page.bring_to_front()

        last_values: dict[str, str] = {}
        started = _now()
        status, note = "skipped", ""
        while True:
            await asyncio.sleep(POLL_SECONDS)
            if self._action in ("skip", "stop"):
                status, note = "skipped", "skipped by you"
                break
            if self._action == "next":
                status, note = "applied", "marked as applied by you"
                break
            pages = [p for p in page.context.pages if not p.is_closed()] or [page]
            done_page = None
            for p in pages:
                if await filler.page_says_done(p):
                    done_page = p
                    break
            if done_page:
                status, page = "applied", done_page
                break
            # keep what you typed into the open / drafted fields, to remember it later
            cur = pages[-1]
            if waiting:
                last_values.update(await filler.read_values(cur, list(waiting)))
            # multi-step forms: new fields appeared -> fill them too
            new = [f for f in await filler.collect(cur) if f.fid not in done_fids]
            if new and cur.url != "about:blank":
                rep2, w2 = await filler.fill_page(cur, ctx, done_fids)
                waiting.update(w2)
                for k in ("filled", "drafted", "open", "consent", "uploaded"):
                    self.report[k] = self.report.get(k, []) + getattr(rep2, k)
                self._emit(f"{job.company}: next step filled", report=self.report)
            if (_now() - started).total_seconds() > JOB_TIMEOUT_MINUTES * 60:
                status, note = "skipped", f"no confirmation after {JOB_TIMEOUT_MINUTES} minutes"
                break

        # learn: every question you answered (or a draft you kept) is reused next time
        learned = 0
        drafts = {d["fid"]: d["value"] for d in self.report.get("drafted", [])}
        for fid, f in waiting.items():
            val = last_values.get(fid, "")
            if val and f.label and f.type != "file":
                P.remember(f.label, val, "claude" if drafts.get(fid) == val else "you")
                learned += 1

        shot = ""
        if status == "applied":
            try:
                d = Path(settings.data_dir) / "applications"
                d.mkdir(parents=True, exist_ok=True)
                shot = str(d / f"{date.today().isoformat()}_{jid}_{(job.company or 'job')[:30].replace(' ', '_')}.png")
                await page.screenshot(path=shot, full_page=True)
            except Exception:
                shot = ""
            _mark_applied(jid, note or "Applied with the apply assistant")
        with db.session_scope() as s:
            att = s.get(m.ApplyAttempt, att_id)
            att.status, att.finished_at, att.form_url = status, _now(), self.report.get("form_url", "")
            att.filled = len(self.report.get("filled", [])) + len(self.report.get("uploaded", []))
            att.open_questions = [o["label"] for o in self.report.get("open", [])]
            att.screenshot, att.note = shot, note
            s.add(att)
        self.history.append({"id": jid, "company": job.company, "title": job.title, "status": status,
                             "note": note, "learned": learned, "screenshot": shot})
        self._emit(f"{job.company}: {'applied ✓' if status == 'applied' else 'skipped'}"
                   + (f" – {learned} new answer(s) saved" if learned else ""), result=status)

    async def _to_form(self, page):
        """A job-board posting -> click its Apply button (new tab or same tab) to reach the form."""
        fields = await filler.collect(page)
        if sum(1 for f in fields if f.type in ("text", "email", "tel", "file")) >= 3:
            return page
        bid = await filler.find_apply_link(page)
        if not bid:
            return page
        ctx = page.context
        before = len(ctx.pages)
        try:
            await page.click(f'[data-ja-btn="{bid}"]', timeout=8000)
        except Exception:
            return page
        await page.wait_for_timeout(3500)
        if len(ctx.pages) > before:
            page = ctx.pages[-1]
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1500)
        return page


def _mark_applied(job_id: int, note: str) -> None:
    with db.session_scope() as s:
        job = s.get(m.Job, job_id)
        if job:
            job.status = m.JobStatus.APPLIED
            s.add(job)
        a = s.exec(select(m.Application).where(m.Application.job_id == job_id)).first() or m.Application(job_id=job_id)
        a.applied_at = date.today()
        if note and note not in (a.notes or ""):
            a.notes = ((a.notes + "\n") if a.notes else "") + note
        s.add(a)


SESSION = ApplySession()
