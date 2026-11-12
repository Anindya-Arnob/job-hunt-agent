"""Collect jobs from sites that block automated browsers (Glassdoor, Indeed) in YOUR Chrome.

The app cannot search these sites itself: they refuse automated browsers even after a human check.
So the Chrome extension does it in a normal tab of your browser, where you pass the check or log in:

1. `start()` makes the list of searches (the same ones the app would run) and the app opens the first one.
2. In that tab the extension waits until the job list shows (you do the check / login meanwhile), then sends
   the page to `page()`. The app reads the job cards with the same parser it uses for its own searches.
3. For the new jobs that pass your filters, the extension opens their pages in the background with your
   session (`detail()`), so the descriptions are read too.
4. `page_done()` stores the jobs (duplicates merged, filters applied), sorts and scores them, and gives the
   next search; the extension moves the same tab there after a short pause.

Nothing is submitted, nothing is clicked on your behalf except moving to the next search address.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from sqlmodel import select

from . import db, events, models as m
from .browser.reader import Snapshot
from .schemas import Query, RawJob
from .textutil import normalize_url

log = logging.getLogger(__name__)

SITES = ("glassdoor", "indeed")
PAGES = 2                        # result pages per search (only when a site has few searches)
MAX_PER_SITE = 20                # search pages per site: one collection stays well under an hour
MAX_DETAILS = 30                 # job pages read per search page
_lock = threading.Lock()
SESSION: dict = {}


def _snap(url: str, html: str) -> Snapshot:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return Snapshot(url=url, final_url=url, status=200, title=title, text=text, html=html)


def site_of(url: str) -> Optional[str]:
    host = urlparse(url or "").netloc.lower()
    for s in SITES:
        if s in host:
            return s
    return None


def _profile(filter_profile_id: Optional[int]):
    with db.session_scope() as s:
        fp = s.get(m.FilterProfile, filter_profile_id) if filter_profile_id else None
        fp = fp or s.exec(select(m.FilterProfile).where(m.FilterProfile.is_default == True)).first()  # noqa: E712
        fp = fp or s.exec(select(m.FilterProfile)).first()
        if fp:
            s.expunge(fp)
        return fp


def spread(qs: list[Query]) -> list[Query]:
    """Round-robin over (role, location), so a capped list still covers every role and place
    instead of 20 wordings of the first role in the first city."""
    groups: dict = {}
    for q in qs:
        groups.setdefault((q.role or q.keywords, q.location), []).append(q)
    out, lists = [], list(groups.values())
    while any(lists):
        for g in lists:
            if g:
                out.append(g.pop(0))
    return out


def plan(sites: list[str], filter_profile_id: Optional[int] = None, pages: Optional[int] = None) -> list[dict]:
    from .agents.sites import SITES as SPECS
    pages = pages or PAGES
    from .steps import planner
    fp = _profile(filter_profile_id)
    if not fp:
        return []
    out = []
    for site in sites:
        if site not in SITES:
            continue
        spec = SPECS[site]
        qs = planner.plan_site_queries(site, fp.roles, fp.job_types, fp.locations, fp.days, fp.work_modes,
                                       fp.keywords_in)
        qs = spread(qs)
        per_q = pages if len(qs) * pages <= MAX_PER_SITE else 1
        added = 0
        for q in qs:
            if added >= MAX_PER_SITE:
                break
            label = f"{spec.label}: {q.keywords[:60]} – {q.location}"
            for p in range(per_q):
                added += 1
                out.append({"site": site, "url": spec.search_url(spec, q, p, "guest"), "page": p,
                            "label": label + (f" (page {p + 1})" if p else ""), "query": q.model_dump()})
    return out


def public() -> dict:
    s = SESSION
    if not s:
        return {"active": False}
    q = s["queue"]
    cur = q[s["idx"]] if s["idx"] < len(q) else None
    return {"active": s["status"] == "running", "id": s["id"], "status": s["status"], "run_id": s["run_id"],
            "done": s["idx"], "total": len(q), "current": cur and {"site": cur["site"], "label": cur["label"],
                                                                     "url": cur["url"]},
            "counts": s["counts"], "last_seen": s.get("last_seen"), "waiting": s.get("waiting", ""),
            "started": s["started"]}


def start(sites: list[str], filter_profile_id: Optional[int] = None) -> dict:
    queue = plan(sites, filter_profile_id)
    if not queue:
        raise ValueError("No searches to run: check your search profile (roles and locations).")
    with db.session_scope() as s:
        run = m.Run(kind="collect", status="running", filter_profile_id=filter_profile_id, counts={}, errors=[], log=[])
        s.add(run)
        s.flush()
        rid = run.id
    with _lock:
        SESSION.clear()
        SESSION.update(id=f"c{rid}", run_id=rid, queue=queue, idx=0, status="running", fp_id=filter_profile_id,
                       counts={"pages": 0, "found": 0, "new": 0, "duplicates": 0, "excluded": 0, "described": 0,
                               "per_site": {}},
                       pending={}, started=datetime.now().astimezone().isoformat(), last_seen=None, waiting="")
    _emit(f"Collecting in your Chrome: {len(queue)} search pages on {', '.join(sorted({q['site'] for q in queue}))}")
    return {**public(), "url": queue[0]["url"]}


def _emit(msg: str, **kw) -> None:
    if SESSION.get("run_id"):
        events.emit({"type": "collect", "message": msg, **kw}, run_id=SESSION["run_id"])


def heartbeat(waiting: str = "") -> dict:
    """The extension is on the page and waiting (a human check, a login, the list loading)."""
    if SESSION:
        SESSION["last_seen"] = time.time()
        if waiting != SESSION.get("waiting"):
            SESSION["waiting"] = waiting
            if waiting:
                _emit(f"Waiting for you in Chrome: {waiting}")
    return public()


def page(url: str, html: str) -> dict:
    """A search page from your browser: read its job cards. Returns the job pages to read next."""
    from .agents.sites import SITES as SPECS
    from .steps.clean import known_url_keys, prefilter_reason
    if not SESSION or SESSION["status"] != "running":
        raise ValueError("No collection is running. Start it on the app's Search screen.")
    SESSION["last_seen"] = time.time()
    site = site_of(url)
    cur = SESSION["queue"][SESSION["idx"]] if SESSION["idx"] < len(SESSION["queue"]) else None
    if not site or not cur:
        return {"details": [], "jobs": 0}
    spec = SPECS[site]
    q = Query.model_validate(cur["query"]) if cur.get("site") == site else Query(site=site, keywords="", location="")
    jobs, total = spec.parse_results(spec, _snap(url, html), q)
    for j in jobs:
        j.mode = "browser"
    fp = _profile(SESSION.get("fp_id")) or m.FilterProfile(name="default")
    known = known_url_keys()
    details = []
    for j in jobs:
        k = normalize_url(j.url)
        if k in SESSION["pending"]:
            continue
        SESSION["pending"][k] = j
        reason = prefilter_reason(j, fp)
        if reason:
            j.prefilter_reason = reason
        elif k not in known and len(details) < MAX_DETAILS:
            details.append(spec.detail_url(spec, j, "guest"))
    c = SESSION["counts"]
    c["found"] += len(jobs)
    ps = c["per_site"].setdefault(site, {"pages": 0, "found": 0, "new": 0})
    ps["pages"] += 1
    ps["found"] += len(jobs)
    _emit(f"{spec.label}: {len(jobs)} jobs on '{cur['label']}'" + (f" ({total} in total)" if total else "")
          + (f", reading {len(details)} new job pages" if details else ""))
    return {"details": details, "jobs": len(jobs), "total": total}


def detail(url: str, html: str) -> dict:
    """A job page read with your session: keep its description."""
    from .agents.extract import main_text
    from .agents.sites import SITES as SPECS
    from .browser.reader import detect_block
    site = site_of(url)
    if not SESSION or not site:
        return {"ok": False}
    SESSION["last_seen"] = time.time()
    snap = _snap(url, html)
    try:
        if detect_block(snap) in ("captcha", "login_wall", "blocked"):
            return {"ok": False, "blocked": True}
    except Exception:
        pass
    spec = SPECS[site]
    text = main_text(snap, spec.detail_selectors)
    k = normalize_url(url)
    j = SESSION["pending"].get(k)
    if j is None:          # Indeed: viewjob?jk=.. is the key itself; Glassdoor: same url
        j = next((x for kk, x in SESSION["pending"].items() if kk == k or normalize_url(spec.detail_url(spec, x, "guest")) == k), None)
    if j is not None and text and len(text) > 150:
        j.description = text
        SESSION["counts"]["described"] += 1
        return {"ok": True}
    return {"ok": False}


def page_done() -> dict:
    """Store this page's jobs and move on to the next search."""
    from .mail.service import _classify_and_score
    from .steps.clean import ingest
    if not SESSION or SESSION["status"] != "running":
        return {**public(), "next": None}
    raws: list[RawJob] = list(SESSION["pending"].values())
    SESSION["pending"] = {}
    fp = _profile(SESSION.get("fp_id")) or m.FilterProfile(name="default")
    if raws:
        counts = ingest(raws, SESSION["run_id"], fp)
        new_ids = counts.get("new_ids", [])
        c = SESSION["counts"]
        c["new"] += len(new_ids)
        c["duplicates"] += counts.get("duplicates", 0) + counts.get("previously_deleted", 0)
        c["excluded"] += counts.get("excluded", 0)
        site = raws[0].source
        c["per_site"].setdefault(site, {"pages": 0, "found": 0, "new": 0})["new"] += len(new_ids)
        threading.Thread(target=_classify_and_score, args=(new_ids, fp), daemon=True).start()
    SESSION["counts"]["pages"] += 1
    SESSION["idx"] += 1
    _save_run()
    if SESSION["idx"] >= len(SESSION["queue"]):
        return finish()
    nxt = SESSION["queue"][SESSION["idx"]]
    return {**public(), "next": nxt["url"], "pause_ms": random.randint(3000, 7000)}


def skip() -> dict:
    """Leave the current search (e.g. a check that never goes away) and go to the next one."""
    if SESSION and SESSION["status"] == "running":
        SESSION["pending"] = {}
        _emit(f"Skipped: {SESSION['queue'][SESSION['idx']]['label']}")
        SESSION["idx"] += 1
        if SESSION["idx"] >= len(SESSION["queue"]):
            return finish()
        return {**public(), "next": SESSION["queue"][SESSION["idx"]]["url"], "pause_ms": 500}
    return {**public(), "next": None}


def finish(status: str = "done") -> dict:
    if not SESSION:
        return {"active": False, "next": None}
    if SESSION["status"] != "running":            # already finished (Stop pressed twice, a late page)
        return {**public(), "next": None}
    if SESSION["pending"]:
        SESSION["idx"] = min(SESSION["idx"], len(SESSION["queue"]))
    SESSION["status"] = status
    _save_run(final=True)
    c = SESSION["counts"]
    _emit(f"Collection {'finished' if status == 'done' else 'stopped'}: {c['found']} jobs seen, {c['new']} new, "
          f"{c['duplicates']} already known")
    return {**public(), "next": None}


def stop() -> dict:
    return finish("stopped")


def _save_run(final: bool = False) -> None:
    with db.session_scope() as s:
        r = s.get(m.Run, SESSION["run_id"])
        if not r:
            return
        c = dict(SESSION["counts"])
        r.counts = {"found": c["found"], "new": c["new"], "duplicates": c["duplicates"], "excluded": c["excluded"],
                    "descriptions read": c["described"], "pages": c["pages"]}
        if final:
            r.status = "done" if SESSION["status"] == "done" else "failed"
            r.finished_at = datetime.now().astimezone()
            if SESSION["status"] != "done":
                r.errors = list(r.errors or []) + ["Stopped in Chrome"]
        s.add(r)
