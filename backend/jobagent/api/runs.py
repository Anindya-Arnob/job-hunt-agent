"""Starts runs in the background and keeps track of them."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from sqlmodel import select

from .. import db, events, llm, models as m
from ..cv.service import get_main
from ..graphs import build_graph, search_graph
from ..steps.score import score_job, store_score

log = logging.getLogger(__name__)
_tasks: dict[int, asyncio.Task] = {}


class Busy(RuntimeError):
    pass


# jobs whose CV is being built right now: job id -> {"run_id", "started"}. Every screen shows these, and the
# same job is never built twice at the same time (that cost tokens twice and nobody could see it).
BUILDING: dict[int, dict] = {}


def building() -> dict[int, dict]:
    for jid in [j for j, b in BUILDING.items() if b["run_id"] not in _tasks or _tasks[b["run_id"]].done()]:
        BUILDING.pop(jid, None)
    return BUILDING


def active_builds() -> list[dict]:
    """For the screens: each job being built, the run, and what that run said last about it."""
    out = []
    for jid, b in sorted(building().items(), key=lambda x: x[1]["started"]):
        evs = [e for e in events.recent(b["run_id"], 300) if e.get("type") == "build"]
        mine = [e for e in evs if e.get("job_id") == jid]
        current = next((e.get("job_id") for e in reversed(evs) if e.get("job_id")), None)
        with db.session_scope() as ss:
            job = ss.get(m.Job, jid)
            company, title = (job.company, job.title) if job else ("", "")
        out.append({"job_id": jid, "run_id": b["run_id"], "started": b["started"], "company": company, "title": title,
                    "in_run": sum(1 for x in BUILDING.values() if x["run_id"] == b["run_id"]),
                    "state": "building" if current == jid else ("waiting" if not mine else "building"),
                    "last": (mine[-1].get("message") if mine else "waiting for its turn")[:200],
                    "attempt": sum(1 for e in mine if str(e.get("message", "")).startswith("CV attempt"))})
    return out


def running(kind: Optional[str] = None) -> list[int]:
    with db.session_scope() as s:
        q = select(m.Run).where(m.Run.status == "running")
        if kind:
            q = q.where(m.Run.kind == kind)
        return [r.id for r in s.exec(q).all()]


def close_stale_runs() -> int:
    """At start no run can still be going: one left on 'running' was cut off when the app closed.
    Without this the Build queue showed it as running forever and kept the Build button disabled."""
    n = 0
    with db.session_scope() as s:
        for r in s.exec(select(m.Run).where(m.Run.status == "running")).all():
            r.status = "failed"
            r.finished_at = r.finished_at or datetime.now().astimezone()
            r.errors = list(r.errors or []) + ["Stopped: the app was closed while this was running. Start it again."]
            s.add(r)
            n += 1
    return n


def _track(run_id: int, coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _tasks[run_id] = t

    def done(task: asyncio.Task):
        _tasks.pop(run_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("run %s ended with error: %s", run_id, exc)
    t.add_done_callback(done)


def _new_run(kind: str, filter_profile_id: Optional[int] = None) -> int:
    with db.session_scope() as s:
        r = m.Run(kind=kind, status="running", filter_profile_id=filter_profile_id)
        s.add(r)
        s.flush()
        r.thread_id = f"{kind}-{r.id}"
        s.add(r)
        return r.id


async def start_search(filter_profile_id: int) -> int:
    if any(t for rid, t in _tasks.items() if not t.done() and _kind(rid) == "search"):
        raise Busy("A search is already running.")
    run_id = _new_run("search", filter_profile_id)
    _track(run_id, search_graph.run_search(filter_profile_id, run_id=run_id))
    return run_id


async def resume_search(run_id: int) -> int:
    with db.session_scope() as s:
        r = s.get(m.Run, run_id)
        fid = r.filter_profile_id
        r.status = "running"
        s.add(r)
    _track(run_id, search_graph.run_search(fid, run_id=run_id, resume=True))
    return run_id


def _kind(run_id: int) -> str:
    with db.session_scope() as s:
        r = s.get(m.Run, run_id)
        return r.kind if r else ""


def _register(run_id: int, job_ids: list[int]) -> None:
    import time
    for j in job_ids:
        BUILDING[j] = {"run_id": run_id, "started": time.time()}


async def start_build(job_ids: Optional[list[int]], cv_profile_id: Optional[int] = None,
                      rebuild: bool = False) -> tuple[int, list[int]]:
    """Returns the run and the jobs left out because they are being built already."""
    active = building()
    already = [j for j in (job_ids or []) if j in active]
    todo = [j for j in (job_ids or []) if j not in active]
    if job_ids and not todo:
        return active[already[0]]["run_id"], already
    run_id = _new_run("build")
    _register(run_id, todo)
    _track(run_id, build_graph.start_build(todo or None, cv_profile_id, rebuild, run_id=run_id))
    return run_id, already


async def resume_build(run_id: int, job_ids: list[int]) -> int:
    active = building()
    todo = [j for j in job_ids if j not in active]
    _register(run_id, todo)
    _track(run_id, build_graph.resume_build(run_id, todo))
    return run_id


async def cancel(run_id: int) -> bool:
    t = _tasks.get(run_id)
    if t and not t.done():
        t.cancel()
    llm.kill_run(run_id)                       # a Claude call already running stops too (no more tokens)
    for jid in [j for j, b in BUILDING.items() if b["run_id"] == run_id]:
        BUILDING.pop(jid, None)
    with db.session_scope() as s:
        r = s.get(m.Run, run_id)
        if r and r.status in ("running", "paused"):
            r.status, r.finished_at = "cancelled", datetime.now().astimezone()
            s.add(r)
    events.emit({"type": "run_cancelled", "message": "Run cancelled"}, run_id=run_id)
    return True


def rescore_sync(scope: str = "all") -> int:
    """Re-score jobs against the current main CV. scope: all / apply / outdated."""
    main = get_main()
    if not main:
        raise ValueError("No main CV")
    prof, vid = main
    run_id = _new_run("rescore")
    llm.set_current_run(run_id)
    with db.session_scope() as s:
        q = select(m.Job, m.Score).join(m.Score, m.Score.job_id == m.Job.id, isouter=True).where(
            m.Job.status.in_([m.JobStatus.SCORED, m.JobStatus.NEW, m.JobStatus.NEEDS_REVIEW, m.JobStatus.BUILT]))
        rows = s.exec(q).all()
    jobs = []
    for job, sc in rows:
        if scope == "apply" and (not sc or sc.verdict == "Skip"):
            continue
        if scope == "outdated" and sc and not sc.is_outdated:
            continue
        jobs.append(job)
    n = 0
    for i, job in enumerate(jobs, 1):
        try:
            store_score(job.id, score_job(job, prof), vid)
            n += 1
        except llm.BudgetExceeded as e:
            events.emit({"type": "progress", "message": str(e), "level": "warn"}, run_id=run_id)
            break
        if i % 10 == 0:
            events.emit({"type": "progress", "message": f"Re-scored {i}/{len(jobs)}"}, run_id=run_id)
    with db.session_scope() as s:
        r = s.get(m.Run, run_id)
        r.status, r.finished_at, r.counts = "done", datetime.now().astimezone(), {"rescored": n}
        r.cost_usd = round(llm.cost_tracker.run_cost.get(run_id, 0.0), 4)
        s.add(r)
    events.emit({"type": "run_done", "kind": "rescore", "message": f"Re-scored {n} jobs"}, run_id=run_id)
    return n
