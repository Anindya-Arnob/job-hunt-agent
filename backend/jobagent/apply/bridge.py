"""Bridge for the browser extension: you apply in YOUR OWN Chrome / Edge, the app decides what to fill.

The extension reads the form on the page you are looking at and sends the fields here. The app
answers with what to put in each one (same rules as always: your details, saved answers, your CV,
Claude drafts for open questions, nothing guessed). When the page says the application was received,
the extension reports it and the job is marked Applied.
"""
from __future__ import annotations

from ..textutil import strip_gender

import base64
import mimetypes
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import select

from .. import db, events, models as m
from ..config import settings
from . import profile as P
from .draft import make_batch_answerer, make_drafter
from .fields import Context, Field, claude_rest, decide
from .session import _mark_applied, cv_text

_lock = threading.Lock()
ACTIVE: dict = {"job_id": None, "set_at": None, "attempt_id": None}
_ACTIVE_KEY = "ext_active_job"


def _load_active() -> None:
    """The job you are applying to survives an app restart."""
    import json
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, _ACTIVE_KEY)
            if row and row.value:
                ACTIVE.update(json.loads(row.value))
    except Exception:
        pass


def _save_active() -> None:
    import json
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, _ACTIVE_KEY) or m.AppSetting(key=_ACTIVE_KEY)
            row.value = json.dumps(ACTIVE)
            s.add(row)
    except Exception:
        pass


_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        _load_active()
_ctx_cache: dict[int, Context] = {}


def docs_for(jid: int) -> dict:
    out = {}
    with db.session_scope() as s:
        for d in s.exec(select(m.Document).where(m.Document.job_id == jid)).all():
            path = d.pdf_path if d.pdf_path and Path(d.pdf_path).exists() else ""   # forms get the PDF only
            if path and Path(path).exists():
                out[d.kind] = path
    return out


def letter_text(jid: int) -> str:
    """The cover letter written for this job (its paragraphs) - the best source for 'motivation' questions."""
    with db.session_scope() as s:
        d = s.exec(select(m.Document).where(m.Document.job_id == jid, m.Document.kind == "letter")).first()
        content = (d.content or {}) if d else {}
    paras = content.get("paragraphs") or []
    if paras:
        return "\n\n".join(paras)
    if d and d.docx_path and Path(d.docx_path).exists():           # letters from before the app: read the file
        try:
            from docx import Document as Docx
            return "\n".join(p.text for p in Docx(d.docx_path).paragraphs if len(p.text) > 40)
        except Exception:
            pass
    if d and d.pdf_path and Path(d.pdf_path).exists():
        try:
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(d.pdf_path).pages)[:5000]
        except Exception:
            return ""
    return ""


def match(url: str, title: str = "") -> list[dict]:
    """Which of your jobs is this page about? Company name found in the page's address or title."""
    import re
    from urllib.parse import urlparse
    host = urlparse(url or "").netloc.lower()
    hay = re.sub(r"[^a-z0-9]", "", host + " " + (title or "").lower())
    tokens = set(re.findall(r"[a-z0-9]+", host + " " + (title or "").lower()))
    generic = {"gmbh", "ag", "se", "kg", "co", "inc", "ltd", "llc", "group", "gruppe", "the", "deutschland", "germany",
               "holding", "und", "and", "deutsche", "technologies", "technology", "solutions", "systems", "consulting",
               "digital", "software", "services", "international", "careers", "career", "jobs", "europe", "berlin",
               "munich", "germany", "company", "labs", "research", "engineering", "data", "global"}
    out = []
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job).where(m.Job.status.in_(["built", "applied"]))).all()
        for j in jobs:
            words = [w for w in re.findall(r"[a-z0-9]+", (j.company or "").lower()) if w not in generic and len(w) > 1]
            if not words:
                continue
            hits = sum(1 for w in words if (len(w) >= 4 and w in hay) or w in tokens)
            if hits:
                out.append({"id": j.id, "title": j.title, "company": j.company, "status": j.status, "hits": hits})
    out.sort(key=lambda x: (x["status"] != "built", -x["hits"]))
    return out[:8]


def context_for(jid: int) -> tuple[m.Job, Context]:
    with db.session_scope() as s:
        job = s.get(m.Job, jid)
        if not job:
            raise KeyError(jid)
        s.expunge(job)
    if jid not in _ctx_cache:
        docs = docs_for(jid)
        ctx = Context(profile=P.load(), job_type=job.job_type, cv_pdf=docs.get("cv", ""), letter_pdf=docs.get("letter", ""),
                      cv_text=cv_text(), company=job.company, title=strip_gender(job.title), job_text=job.full_description or "",
                      letter_text=letter_text(jid))
        ctx.answerer = make_batch_answerer(ctx)          # all open questions of a page in one Claude call
        ctx.drafter = None if ctx.answerer else make_drafter(ctx)
        _ctx_cache[jid] = ctx
    else:
        _ctx_cache[jid].profile = P.load()          # pick up edits in My details right away
    return job, _ctx_cache[jid]


def set_active(jid: Optional[int]) -> dict:
    """Pick the job you apply to in your browser; None = stop (the open attempt is recorded as stopped)."""
    _ensure_loaded()
    with _lock:
        prev = ACTIVE.get("attempt_id")
        if prev:
            with db.session_scope() as s:
                att = s.get(m.ApplyAttempt, prev)
                if att and att.status == "running":
                    att.status, att.finished_at, att.note = "skipped", datetime.now().astimezone(), "stopped by you"
                    s.add(att)
        with db.session_scope() as s:       # attempts left open (app restarted, tab closed): not finished
            for att in s.exec(select(m.ApplyAttempt).where(m.ApplyAttempt.status == "running",
                                                          m.ApplyAttempt.note == "in your browser")).all():
                att.status, att.finished_at, att.note = "skipped", datetime.now().astimezone(), "not finished"
                s.add(att)
        ACTIVE.update(job_id=jid, set_at=datetime.now().astimezone().isoformat(), attempt_id=None)
        _ctx_cache.clear()
        if jid:
            with db.session_scope() as s:
                att = m.ApplyAttempt(job_id=jid, note="in your browser")
                s.add(att)
                s.flush()
                ACTIVE["attempt_id"] = att.id
        _save_active()
    events.emit({"type": "apply", "message": "active job changed", "active": jid})
    return active()


def active() -> dict:
    _ensure_loaded()
    jid = ACTIVE.get("job_id")
    if not jid:
        return {"job": None}
    with db.session_scope() as s:
        j = s.get(m.Job, jid)
        if not j:
            return {"job": None}
        docs = docs_for(jid)
        return {"job": {"id": j.id, "title": j.title, "company": j.company, "link": j.link, "job_type": j.job_type,
                        "has_cv": "cv" in docs, "has_letter": "letter" in docs}}


def decide_fields(jid: int, raw_fields: list[dict]) -> list[dict]:
    job, ctx = context_for(jid)
    out, files = [], 0
    fields, decisions = [], {}
    for r in raw_fields:
        f = Field(**{k: r.get(k, d) for k, d in (("fid", ""), ("tag", "input"), ("type", "text"), ("label", ""),
                                                  ("name", ""), ("placeholder", ""), ("required", False),
                                                  ("options", []), ("value", ""), ("maxlength", 0))})
        idx = files if f.type == "file" else 0
        if f.type == "file":
            files += 1
        fields.append(f)
        decisions[f.fid] = decide(f, ctx, file_index=idx)
    claude_rest(fields, decisions, ctx)
    for f in fields:
        d = decisions[f.fid]
        item = {"fid": f.fid, "action": d.action, "value": d.value, "source": d.source, "why": d.why}
        if d.action == "upload" and d.value:
            item["file"] = {"name": Path(d.value).name, "mime": mimetypes.guess_type(d.value)[0] or "application/pdf",
                            "kind": "letter" if d.value == ctx.letter_pdf else "cv"}
        out.append(item)
    return out


def file_b64(jid: int, kind: str) -> dict:
    path = docs_for(jid).get(kind)
    if not path:
        raise KeyError(kind)
    data = Path(path).read_bytes()
    return {"name": Path(path).name, "mime": mimetypes.guess_type(path)[0] or "application/pdf",
            "b64": base64.b64encode(data).decode()}


def learn(answers: list[dict]) -> int:
    n = 0
    for a in answers:
        q, v = (a.get("question") or "").strip(), (a.get("answer") or "").strip()
        if q and v:
            P.remember(q, v, "claude" if a.get("kept_draft") else "you")
            n += 1
    return n


def done(jid: int, url: str = "", screenshot_b64: str = "", answers: Optional[list] = None, how: str = "") -> dict:
    learned = learn(answers or [])
    shot = ""
    if screenshot_b64:
        try:
            d = Path(settings.data_dir) / "applications"
            d.mkdir(parents=True, exist_ok=True)
            with db.session_scope() as s:
                comp = (s.get(m.Job, jid).company or "job")[:30].replace(" ", "_")
            p = d / f"{datetime.now():%Y-%m-%d}_{jid}_{comp}.png"
            p.write_bytes(base64.b64decode(screenshot_b64.split(",", 1)[-1]))
            shot = str(p)
        except Exception:
            shot = ""
    _mark_applied(jid, "Applied in your browser" + (f" ({how})" if how else ""))
    with db.session_scope() as s:
        att = s.get(m.ApplyAttempt, ACTIVE.get("attempt_id")) if ACTIVE.get("job_id") == jid and ACTIVE.get("attempt_id") else None
        att = att or m.ApplyAttempt(job_id=jid, note="in your browser")
        att.status, att.finished_at, att.form_url, att.screenshot = "applied", datetime.now().astimezone(), url, shot
        s.add(att)
    if ACTIVE.get("job_id") == jid:
        ACTIVE.update(job_id=None, attempt_id=None)
        _save_active()
    events.emit({"type": "apply", "message": "applied in your browser", "result": "applied", "job_id": jid})
    return {"ok": True, "learned": learned, "screenshot": shot}
