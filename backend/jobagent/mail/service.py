"""Keeps an eye on your Gmail: every few minutes it reads new job mails and updates your tracker.

confirmation -> job marked Applied (with the mail's date)      rejection -> Rejected
interview / test -> Interview, shown as "to do" on the Inbox page   offer -> Offer
job alert (StepStone, Glassdoor, XING, LinkedIn, company talent pools ...) -> the jobs are added, checked and scored
A company mail about a job the app does not know yet is added to your tracker too.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import select

from .. import db, events, models as m
from ..textutil import norm_company, norm_title
from . import reader, sort

log = logging.getLogger(__name__)
KEY = "mail_account"
_lock = threading.Lock()
STATE = {"running": False, "last_result": None}
STATUS_KINDS = ("confirmation", "rejection", "interview", "assessment", "offer")
RANK = {m.JobStatus.NEW: 0, m.JobStatus.NEEDS_REVIEW: 0, m.JobStatus.EXCLUDED: 0, m.JobStatus.SCORED: 1,
        m.JobStatus.BUILT: 2, m.JobStatus.APPLIED: 3, m.JobStatus.REJECTED: 4, m.JobStatus.INTERVIEW: 5,
        m.JobStatus.OFFER: 6}
DEFAULTS = {"address": "", "app_password": "", "enabled": False, "interval_min": 10, "first_days": 60,
            "add_jobs": True, "alert_max_age_days": 7, "last_uid": 0, "uidvalidity": "", "last_check": None,
            "last_error": "", "checks": 0, "backlog": [], "min_score": 60}


# ------------------------------------------------------------------ settings ---
def config() -> dict:
    with db.session_scope() as s:
        row = s.get(m.AppSetting, KEY)
        data = json.loads(row.value) if row and row.value else {}
    return {**DEFAULTS, **data}


def save_config(patch: dict) -> dict:
    cfg = config()
    for k, v in patch.items():
        if k in DEFAULTS and not (k == "app_password" and not v):
            cfg[k] = v
    if "address" in patch and patch["address"] != config()["address"]:
        cfg["last_uid"], cfg["uidvalidity"] = 0, ""            # another mailbox: start fresh
    with db.session_scope() as s:
        row = s.get(m.AppSetting, KEY) or m.AppSetting(key=KEY)
        row.value = json.dumps(cfg, default=str)
        s.add(row)
    return cfg


def public() -> dict:
    c = config()
    with db.session_scope() as s:
        rows = s.exec(select(m.MailEvent)).all()
        counts = {}
        for r in rows:
            counts[r.kind] = counts.get(r.kind, 0) + 1
        todo = sum(1 for r in rows if r.kind in ("interview", "assessment", "offer") and not r.done)
    from .. import scheduler
    nxt = scheduler.next_mail_check()
    return {"address": c["address"], "has_password": bool(c["app_password"]), "enabled": c["enabled"],
            "interval_min": c["interval_min"], "first_days": c["first_days"], "add_jobs": c["add_jobs"],
            "min_score": c.get("min_score", 60),
            "last_check": c["last_check"], "last_error": c["last_error"], "running": STATE["running"],
            "last_result": STATE["last_result"], "counts": counts, "todo": todo, "older_waiting": len(c.get("backlog") or []),
            "next_check": nxt.isoformat() if nxt else None}


# ------------------------------------------------------------------ matching ---
def _tokens(s: str) -> set[str]:
    return {w for w in norm_title(s).split() if len(w) > 2 and w not in ("und", "and", "the", "for", "mit", "with")}


def _company_hit(company: str, sender: str, job_company: str) -> float:
    jc = norm_company(job_company)
    if not jc:
        return 0.0
    words = [w for w in jc.split() if len(w) > 2 and w not in ("group", "gruppe", "deutschland", "germany", "holding")]
    cand = norm_company(company)
    if cand and (cand == jc or cand in jc or jc in cand):
        return 1.0
    hay = f"{cand} {sender.lower()}"
    hits = sum(1 for w in words if w in hay)
    return hits / max(1, len(words)) if hits else 0.0


def match_job(company: str, role: str, sender: str) -> Optional[int]:
    with db.session_scope() as s:
        jobs = s.exec(select(m.Job).where(m.Job.status.in_([m.JobStatus.SCORED, m.JobStatus.BUILT, m.JobStatus.APPLIED,
                                                             m.JobStatus.INTERVIEW, m.JobStatus.REJECTED,
                                                             m.JobStatus.OFFER, m.JobStatus.NEEDS_REVIEW]))).all()
        rt = _tokens(role)
        best, best_score = None, 0.0
        for j in jobs:
            c = _company_hit(company, sender, j.company)
            if not c:
                continue
            jt = _tokens(j.title)
            t = len(rt & jt) / len(rt | jt) if rt and jt else 0.0
            score = c * 2 + t * 3 + RANK.get(j.status, 0) * 0.05
            if rt and jt and t < 0.25 and c < 1:
                continue
            if score > best_score:
                best, best_score = j.id, score
        return best if best_score >= 1.0 else None


# ------------------------------------------------------------------ tracker ---
def _note(app: m.Application, line: str) -> None:
    if line not in (app.notes or ""):
        app.notes = ((app.notes + "\n") if app.notes else "") + line


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def apply_to_tracker(ev: m.MailEvent) -> str:
    """Move the job forward according to the mail. Returns what changed (for the Inbox page)."""
    if ev.kind not in STATUS_KINDS + ("update",):
        return ""
    with db.session_scope() as s:
        job = s.get(m.Job, ev.job_id) if ev.job_id else None
        if not job:
            if ev.kind == "update" or not ev.company:
                return ""
            from ..textutil import dedupe_key
            title = ev.role or "(role from email)"
            job = m.Job(company=ev.company, title=title, sources=["inbox"], links=[], status=m.JobStatus.APPLIED,
                        dedupe_key=dedupe_key(ev.company, title, ""), url_key="",
                        notes="Added from your email (you applied outside the app)")
            s.add(job)
            s.flush()
            ev.job_id = job.id
            created = True
        else:
            created = False
        app = s.exec(select(m.Application).where(m.Application.job_id == job.id)).first() or m.Application(job_id=job.id)
        newer = False
        if ev.received_at and not created:
            for other in s.exec(select(m.MailEvent).where(m.MailEvent.job_id == job.id,
                                                          m.MailEvent.kind.in_(STATUS_KINDS))).all():
                if other.received_at and _aware(other.received_at) > _aware(ev.received_at):
                    newer = True
        day = (ev.received_at or datetime.now().astimezone()).date()
        stamp = day.strftime("%d.%m.%Y")
        old = job.status
        if ev.kind == "confirmation":
            new = m.JobStatus.APPLIED if RANK.get(old, 0) < RANK[m.JobStatus.APPLIED] else old
            app.applied_at = app.applied_at or day
            _note(app, f"{stamp} ✉ Application confirmed by {ev.company or ev.sender_name}")
        elif ev.kind == "rejection":
            new = m.JobStatus.REJECTED
            app.outcome = f"Rejected ({stamp})"
            _note(app, f"{stamp} ✉ Rejection: {ev.summary[:160]}")
        elif ev.kind in ("interview", "assessment"):
            new = m.JobStatus.INTERVIEW if old != m.JobStatus.OFFER else old
            app.outcome = ("Interview invitation" if ev.kind == "interview" else "Test / assessment") + f" ({stamp})"
            app.follow_up_at = app.follow_up_at or day
            _note(app, f"{stamp} ✉ {'Interview' if ev.kind == 'interview' else 'Test'}: {ev.action or ev.summary}"[:220]
                  + (f" – {ev.when_text}" if ev.when_text else ""))
        elif ev.kind == "offer":
            new = m.JobStatus.OFFER
            app.outcome = f"Offer ({stamp})"
            _note(app, f"{stamp} ✉ Offer: {ev.summary[:160]}")
        else:                                                   # update
            new = old
            _note(app, f"{stamp} ✉ {ev.summary[:180]}")
        if ev.kind != "update" and not app.applied_at:
            app.applied_at = day
        if newer:                                              # a later mail already decided the status
            new = old
        job.status = new
        job.updated_at = datetime.now().astimezone()
        s.add(job)
        s.add(app)
    if created:
        return f"added to your tracker as {new}"
    return f"marked {new.capitalize()}" if new != old else ("noted in the tracker" if ev.kind == "update" else "")


# ------------------------------------------------------------------ alerts -> jobs ---
def _platform(sender: str) -> str:
    m_ = re.search(r"(stepstone|glassdoor|xing|linkedin|indeed|monster|jobware|wellfound|join|arbeitsagentur|microsoft)",
                   sender, re.I)
    return (m_.group(1).lower() if m_ else sender.split("@")[-1].split(".")[-2]) if sender else "email"


async def _resolve(urls: list[str]) -> dict:
    """Tracking links (click.stepstone.de/...) -> the real posting address."""
    import httpx
    out = {}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}) as c:
        for u in urls:
            try:
                r = await c.get(u)
                out[u] = str(r.url)
            except Exception:
                out[u] = u
    return out


RESOLVE = _resolve          # test hook


def add_alert_jobs(found: list[dict], source: str) -> list[int]:
    from ..schemas import RawJob
    from ..steps.clean import ingest
    from ..feeds.websearch import fetch_descriptions
    if not found:
        return []
    links = asyncio.run(RESOLVE([j["url"] for j in found]))
    raws = []
    for j in found:
        url = links.get(j["url"], j["url"])
        url = re.sub(r"[?#].*$", "", url) if re.search(r"stepstone|xing|linkedin|glassdoor", url) else url
        raws.append(RawJob(source=f"email:{source}", title=j["title"], company=j.get("company", ""),
                           location=j.get("location", ""), url=url, job_type_hint=j.get("job_type", ""), mode="feed"))
    try:
        asyncio.run(fetch_descriptions(raws, limit=40))
    except Exception as e:
        log.info("alert descriptions: %s", e)
    for r in raws:                                             # "Werkstudent Software Testing & Testautomat..." -> full title
        if r.title.endswith(("...", "…")) and r.description:
            stem = r.title.rstrip(".…").strip()
            m_ = re.search(re.escape(stem) + r"[^\n]{0,80}", r.description)
            if m_:
                r.title = m_.group(0).strip()
    with db.session_scope() as s:
        fp = s.exec(select(m.FilterProfile).where(m.FilterProfile.is_default == True)).first() or \
            s.exec(select(m.FilterProfile)).first()  # noqa: E712
        if fp:
            s.expunge(fp)
    if not fp:
        fp = m.FilterProfile(name="default")
    counts = ingest(raws, None, fp)
    new_ids = counts.get("new_ids", [])
    _classify_and_score(new_ids, fp)
    good, low = keep_good_matches(new_ids)
    STATS.update(found=len(found), duplicates=counts.get("duplicates", 0) + counts.get("previously_deleted", 0),
                 filtered=counts.get("excluded", 0) + (len(new_ids) - len(good) - len(low)), low=len(low), good=len(good))
    return good


STATS: dict = {}


def keep_good_matches(ids: list[int], min_score: Optional[int] = None) -> tuple[list[int], list[int]]:
    """Jobs from mails stay on your Job board only when they fit your CV (score >= min_score);
    the others go to Excluded with the reason, so nothing is lost."""
    min_score = config().get("min_score", 60) if min_score is None else min_score
    good, low = [], []
    with db.session_scope() as s:
        scores = {sc.job_id: sc for sc in s.exec(select(m.Score).where(m.Score.job_id.in_(ids))).all()} if ids else {}
        for jid in ids:
            j = s.get(m.Job, jid)
            if not j or j.status in (m.JobStatus.EXCLUDED,) and j.exclude_reason and not j.exclude_reason.startswith("Low match"):
                continue                                           # excluded by your filters (full-time, field ...)
            sc = scores.get(jid)
            if j.status not in (m.JobStatus.NEW, m.JobStatus.SCORED, m.JobStatus.NEEDS_REVIEW, m.JobStatus.EXCLUDED):
                continue
            if sc and sc.total >= min_score:
                if j.status == m.JobStatus.EXCLUDED:
                    j.status, j.exclude_reason = m.JobStatus.SCORED, ""
                good.append(jid)
            else:
                j.status = m.JobStatus.EXCLUDED
                j.exclude_reason = f"Low match from email (score {sc.total if sc else '–'} < {min_score})"
                low.append(jid)
            s.add(j)
    return good, low


def recheck_email_jobs(min_score: int) -> dict:
    """After changing the minimum score: show / hide the jobs that came from mails again."""
    with db.session_scope() as s:
        ids = [j.id for j in s.exec(select(m.Job).where(m.Job.status.in_([m.JobStatus.NEW, m.JobStatus.SCORED,
                                                                       m.JobStatus.NEEDS_REVIEW, m.JobStatus.EXCLUDED]))).all()
               if any(str(x).startswith("email") for x in (j.sources or []))
               and (j.status != m.JobStatus.EXCLUDED or (j.exclude_reason or "").startswith("Low match"))]
    good, low = keep_good_matches(ids, min_score)
    return {"shown": len(good), "hidden": len(low)}


def _classify_and_score(ids: list[int], fp) -> None:
    from ..cv.service import get_main
    from ..steps.classify import apply_classification, classify_job
    from ..steps.score import score_job, store_score
    for jid in ids:
        try:
            with db.session_scope() as s:
                job = s.get(m.Job, jid)
                c = classify_job(job)
                apply_classification(job, c, fp)
                s.add(job)
        except Exception as e:
            log.info("classify %s: %s", jid, e)
    main = get_main()
    if not main:
        return
    profile, vid = main
    for jid in ids:
        with db.session_scope() as s:
            job = s.get(m.Job, jid)
            if not job or job.status not in (m.JobStatus.NEW, m.JobStatus.NEEDS_REVIEW):
                continue
            s.expunge(job)
        try:
            store_score(jid, score_job(job, profile), vid)
        except Exception as e:
            log.info("score %s: %s", jid, e)


# ------------------------------------------------------------------ the check ---
def _alert_effect(found: list, st: dict) -> str:
    if not found:
        return "no jobs found in it"
    parts = [f"{st.get('good', 0)} good match(es) added to your Job board"]
    if st.get("low"):
        parts.append(f"{st['low']} low match(es) hidden")
    if st.get("filtered"):
        parts.append(f"{st['filtered']} filtered out")
    if st.get("duplicates"):
        parts.append(f"{st['duplicates']} already known")
    return " · ".join(parts)


def _known(msg_id: str) -> bool:
    with db.session_scope() as s:
        return s.exec(select(m.MailEvent).where(m.MailEvent.msg_id == msg_id)).first() is not None


def process(mails: list[reader.Mail], cfg: dict) -> dict:
    mails = [x for x in mails if not _known(x.msg_id)]
    reads = sort.classify(mails) if mails else {}
    res = {"read": len(mails), "status_changes": 0, "new_jobs": 0, "todo": 0, "kinds": {}}
    now = datetime.now(timezone.utc)
    for x in mails:
        r = reads[str(x.uid)]
        if r.kind == "other":
            continue                                            # newsletters & co are not kept
        res["kinds"][r.kind] = res["kinds"].get(r.kind, 0) + 1
        ev = m.MailEvent(msg_id=x.msg_id, uid=x.uid, received_at=x.date, sender=x.sender, sender_name=x.sender_name,
                         subject=x.subject[:300], kind=r.kind, company=r.company[:120], role=r.role[:200],
                         summary=r.summary[:400], action=r.action[:300], when_text=r.when[:120],
                         excerpt="" if r.kind == "account" else sort.redact(x.text)[:1500])
        if r.kind in STATUS_KINDS + ("update",):
            ev.job_id = match_job(r.company, r.role, x.sender)
        if r.kind == "job_alert" and cfg.get("add_jobs", True):
            fresh = not x.date or (now - x.date) <= timedelta(days=cfg.get("alert_max_age_days", 7))
            if fresh:
                try:
                    found = sort.alert_jobs(x, use_claude=res.setdefault("alerts_read", 0) < 20)
                    res["alerts_read"] += 1
                    STATS.clear()
                    ev.job_ids = add_alert_jobs(found, _platform(x.sender))
                    ev.effect = _alert_effect(found, STATS)
                    res["new_jobs"] += len(ev.job_ids)
                except Exception as e:
                    log.warning("alert jobs failed: %s", e)
                    ev.effect = "could not read the jobs"
            else:
                ev.effect = "older alert - jobs not added"
        if r.kind in STATUS_KINDS + ("update",):
            ev.effect = apply_to_tracker(ev)
            if ev.effect.startswith(("marked", "added")):
                res["status_changes"] += 1
        if r.kind in ("interview", "assessment", "offer"):
            res["todo"] += 1
        with db.session_scope() as s:
            s.add(ev)
        events.emit({"type": "mail", "message": f"✉ {r.kind}: {r.company or x.sender_name} – {x.subject[:80]}",
                     "kind": r.kind})
    return res


def check_now() -> dict:
    """One check of the mailbox (runs in a worker thread)."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "error": "A check is already running"}
    STATE["running"] = True
    cfg = config()
    try:
        if not (cfg["address"] and cfg["app_password"]):
            raise reader.AuthError("Connect your Gmail first (address + app password)")
        events.emit({"type": "mail", "message": "Checking your Gmail…"})
        try:
            recheck_email_jobs(cfg.get("min_score", 60))          # jobs added before: only good matches stay listed
        except Exception as e:
            log.info("recheck email jobs: %s", e)
        total = None
        for round_ in range(8):                  # newest mails first; then a few rounds of older ones
            cfg = config()
            mails, uv, top, backlog = reader.fetch(cfg["address"], cfg["app_password"], cfg["last_uid"],
                                                   cfg["uidvalidity"], cfg["first_days"], backlog=cfg.get("backlog") or [])
            res = process(mails, cfg)
            res["older_waiting"] = len(backlog)
            save_config({"last_uid": top, "uidvalidity": uv, "backlog": backlog,
                         "last_check": datetime.now().astimezone().isoformat(),
                         "last_error": "", "checks": cfg.get("checks", 0) + 1})
            if total is None:
                total = dict(res)
            else:
                for k in ("read", "status_changes", "new_jobs", "todo"):
                    total[k] += res[k]
                total["older_waiting"] = res["older_waiting"]
            if not backlog:
                break
            events.emit({"type": "mail", "message": f"Newest mails done – reading {len(backlog)} older job mails…",
                         "result": dict(total)})
        res = total
        STATE["last_result"] = res
        events.emit({"type": "mail", "message": f"Gmail checked: {res['read']} job mails, {res['status_changes']} "
                                                f"tracker update(s), {res['new_jobs']} new job(s)", "result": res})
        return {"ok": True, **res}
    except Exception as e:
        msg = str(e) if isinstance(e, reader.AuthError) else f"Gmail check failed: {e}"
        save_config({"last_error": msg[:300], "last_check": datetime.now().astimezone().isoformat()})
        events.emit({"type": "mail", "message": msg, "level": "warn"})
        return {"ok": False, "error": msg}
    finally:
        STATE["running"] = False
        _lock.release()


async def scheduled_check():
    cfg = config()
    if cfg["enabled"] and cfg["app_password"]:
        await asyncio.to_thread(check_now)


def link_event(event_id: int, job_id: Optional[int]) -> dict:
    with db.session_scope() as s:
        ev = s.get(m.MailEvent, event_id)
        if not ev:
            raise KeyError(event_id)
        ev.job_id = job_id
        s.add(ev)
        s.flush()
        s.expunge(ev)
    eff = apply_to_tracker(ev) if job_id else ""
    with db.session_scope() as s:
        row = s.get(m.MailEvent, event_id)
        row.job_id, row.effect = ev.job_id, eff or row.effect
        s.add(row)
    return {"ok": True, "effect": eff}
