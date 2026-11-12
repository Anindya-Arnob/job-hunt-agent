"""The search graph (LangGraph).

plan_queries -> [site agents + feeds in parallel] -> clean_dedupe -> classify -> score -> export

Every step is checkpointed (SQLite), so a crashed run continues where it stopped.
"""
from __future__ import annotations

import asyncio
import logging
import operator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from sqlmodel import select

from .. import db, events, llm, models as m, progress
from ..agents.site_agent import SiteAgent
from ..agents.sites import SITES
from ..browser import connections
from ..config import settings
from ..cv.service import get_main
from ..feeds import feeds as F
from ..feeds import websearch as W
from ..schemas import Query, RawJob
from ..steps import planner
from ..steps.classify import apply_classification, classify_job
from ..steps.clean import ingest, known_url_keys, prefilter_reason
from ..steps.export import export_run
from ..steps.score import score_job, store_score

log = logging.getLogger(__name__)

BROWSER_SITES = ["linkedin", "glassdoor", "indeed", "stepstone", "xing", "wellfound"]
FEED_SOURCES = {"ats_feeds", "arbeitsagentur", "arbeitnow", "remoteok"}

# Test hooks
READER_FACTORY = None      # (site, mode) -> PageReader
FEED_TRANSPORT = None      # httpx transport
WEB_CALLER = None         # test hook: replaces the Claude web-search call
WEB_HTTP = None           # test hook: httpx client for reading web-search postings
MAX_SPLIT_ROUNDS = 3
WORKERS = 4


class SearchState(TypedDict, total=False):
    run_id: int
    filter_profile_id: int
    site_queries: dict
    feed_queries: list
    raw_jobs: Annotated[list, operator.add]
    outcomes: Annotated[list, operator.add]
    errors: Annotated[list, operator.add]
    counts: dict
    new_ids: list
    export_path: str


def _fp(fp_id: int) -> m.FilterProfile:
    with db.session_scope() as s:
        fp = s.get(m.FilterProfile, fp_id)
        if not fp:
            raise ValueError(f"Filter profile {fp_id} not found")
        return fp


def _emit(state, msg: str, **kw):
    events.emit({"type": "progress", "message": msg, **kw}, run_id=state.get("run_id"))


def _update_run(run_id: int, **fields) -> None:
    with db.session_scope() as s:
        r = s.get(m.Run, run_id)
        if not r:
            return
        for k, v in fields.items():
            setattr(r, k, v)
        s.add(r)


# ------------------------------------------------------------------- nodes ---
def plan_queries(state: SearchState) -> dict:
    fp = _fp(state["filter_profile_id"])
    sq = {}
    for site in BROWSER_SITES:
        if site in (fp.sources or []):
            qs = planner.plan_site_queries(site, fp.roles, fp.job_types, fp.locations, fp.days,
                                           fp.work_modes, fp.keywords_in)
            sq[site] = [q.model_dump() for q in qs]
    fq = planner.plan_feed_queries(fp.roles, fp.job_types, fp.locations) if "arbeitsagentur" in fp.sources else []
    total = sum(len(v) for v in sq.values()) + len(fq)
    srcs = {site: len(qs) for site, qs in sq.items() if qs}
    if FEED_SOURCES & set(fp.sources or []):
        srcs["feeds"] = 0
    if settings.websearch_enabled and (set(W.WEB_SITES) & set(fp.sources or []) or settings.websearch_other_sites):
        srcs["websearch"] = 0
    progress.start(state["run_id"], srcs, no_pages=("feeds", "websearch"))
    _emit(state, f"Planned {total} searches across {len(sq) + (1 if fq else 0)} sources", stage="plan")
    return {"site_queries": sq, "feed_queries": fq}


def fan_out(state: SearchState):
    fp = _fp(state["filter_profile_id"])
    sends = [Send("site_agent", {"run_id": state["run_id"], "filter_profile_id": state["filter_profile_id"],
                                 "site": site, "queries": qs})
             for site, qs in state.get("site_queries", {}).items() if qs]
    if FEED_SOURCES & set(fp.sources or []):
        sends.append(Send("feeds", {"run_id": state["run_id"], "filter_profile_id": state["filter_profile_id"],
                                    "feed_queries": state.get("feed_queries", [])}))
    if settings.websearch_enabled and (set(W.WEB_SITES) & set(fp.sources or []) or settings.websearch_other_sites):
        sends.append(Send("web_search", {"run_id": state["run_id"], "filter_profile_id": state["filter_profile_id"]}))
    return sends or ["clean_dedupe"]


async def web_search(payload: dict) -> dict:
    """Web-search agent: postings found through Claude's web search (works where browsers get blocked)."""
    run_id = payload["run_id"]
    fp = _fp(payload["filter_profile_id"])
    llm.set_current_run(run_id)
    outcomes, errors, jobs = [], [], []
    queries = W.plan_web_queries(fp.roles, fp.job_types, fp.locations, fp.days, fp.sources or [])
    eng = W.engine() if WEB_CALLER is None else "test"
    say = lambda msg, **kw: events.emit({"type": "agent", "site": "websearch", "message": msg, **kw}, run_id=run_id)
    if not eng:
        msg = ("Web search not available: add an Anthropic API key in .env, or install Claude Code "
               "(npm install -g @anthropic-ai/claude-code) and log in once with `claude`.")
        say(msg, level="warn")
        outcomes.append({"site": "websearch", "query": "(setup)", "location": "", "mode": "web", "status": "failed",
                         "reported": None, "collected": 0, "pages": 0, "error": msg})
        progress.source(run_id, "websearch", done=True, jobs=0)
        events.emit({"type": "site_done", "site": "websearch", "jobs": 0, "message": "Web search: not set up"}, run_id=run_id)
        return {"raw_jobs": [], "outcomes": outcomes, "errors": [msg]}
    n_done = [0]
    progress.source(run_id, "websearch", phase="searching", searched=0, searches=len(queries), reads_pages=False)
    say(f"{len(queries)} web searches planned ({'Claude Code' if eng == 'cli' else 'Anthropic API' if eng == 'api' else eng})")

    def done(r: "W.WebResult"):
        q = r.query
        llm.cost_tracker.add(run_id, r.cost_usd if eng == "api" else 0.0)
        status = "failed" if r.error else "ok"
        outcomes.append({"site": q.site, "query": f"{q.group} (web search)", "location": q.location, "mode": "web",
                         "status": status, "reported": None, "collected": len(r.jobs), "pages": r.searches,
                         "error": r.error or (f"{r.dropped} unverified result(s) dropped" if r.dropped else "") or r.note})
        why = r.error or (r.note if not r.jobs else "")
        say(f"{q.label}: {len(r.jobs)} jobs" + (f" - {why}" if why else ""), level="warn" if r.error else None)
        n_done[0] += 1
        progress.source(run_id, "websearch", searched=n_done[0])

    try:
        results = await W.run_all(queries, caller=WEB_CALLER, on_done=done)
        for r in results:
            jobs += r.jobs
        n = await W.fetch_descriptions(jobs, client=WEB_HTTP)
        if n:
            say(f"read the full description of {n} postings")
    except W.WebSearchUnavailable as e:
        errors.append(f"web search: {e}")
        say(f"web search stopped: {e}", level="warn")
    except llm.BudgetExceeded as e:
        errors.append(f"web search: {e}")
    progress.source(run_id, "websearch", done=True, jobs=len(jobs))
    events.emit({"type": "site_done", "site": "websearch", "jobs": len(jobs),
                 "message": f"Web search: {len(jobs)} jobs"}, run_id=run_id)
    return {"raw_jobs": [j.model_dump(mode="json") for j in jobs], "outcomes": outcomes, "errors": errors}


def _get_start_mode(site: str) -> str:
    """'window' once a site has needed a visible browser window (its bot check blocks headless browsers)."""
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, f"start_mode:{site}")
            return row.value if row and row.value in ("guest", "window", "manual") else "guest"
    except Exception:
        return "guest"


def _set_start_mode(site: str, mode: str) -> None:
    try:
        with db.session_scope() as s:
            row = s.get(m.AppSetting, f"start_mode:{site}") or m.AppSetting(key=f"start_mode:{site}")
            row.value = mode
            s.add(row)
    except Exception:
        pass


async def site_agent(payload: dict) -> dict:
    site, run_id = payload["site"], payload["run_id"]
    fp = _fp(payload["filter_profile_id"])
    llm.set_current_run(run_id)
    status = connections.get_status(site)

    async def on_captcha(site_name: str, url: str) -> bool:
        events.emit({"type": "captcha", "site": site_name, "url": url,
                     "message": f"{SITES[site_name].label} wants a 'verify you are human' check. A browser window opened – "
                                f"solve it there; the search continues by itself (or press Solved)."},
                    run_id=run_id)
        return await events.wait_for(f"captcha:{run_id}:{site_name}", timeout=300)

    base = {"searched": 0, "new": 0, "last_new": 0}       # earlier rounds (narrower searches add a round)

    def on_progress(**kw):
        if "searched" in kw:
            base["last_new"] = kw.get("new_seen", 0)
            kw.update(searched=base["searched"] + kw["searched"], searches=base["searched"] + kw["searches"],
                      new_seen=base["new"] + base["last_new"])
        progress.source(run_id, site, **kw)

    agent = SiteAgent(site, connection_status=status, known_url_keys=known_url_keys(),
                      emit=lambda e: events.emit(e, run_id=run_id), on_captcha=on_captcha,
                      prefilter=lambda j: prefilter_reason(j, fp), reader_factory=READER_FACTORY,
                      start_mode=_get_start_mode(site), on_progress=on_progress)
    queries = [Query(**q) for q in payload["queries"]]
    jobs: list[RawJob] = []
    outcomes: list[dict] = []
    errors: list[str] = []
    try:
        for round_ in range(MAX_SPLIT_ROUNDS + 1):
            if not queries:
                break
            res = await agent.run(queries)
            base["searched"] += len(queries)
            base["new"] += base["last_new"]
            base["last_new"] = 0
            jobs += res.jobs
            if agent.refuses_automation:
                _set_start_mode(site, "manual")      # blocks automated browsers: stop trying, show links instead
                events.emit({"type": "agent", "site": site, "level": "warn", "message":
                             f"{SITES[site].label} blocks automated browsers even after the check - from now on it is not "
                             "searched automatically. Its searches are listed as links on the Search screen."}, run_id=run_id)
            elif agent.start_mode == "window":
                _set_start_mode(site, "window")      # next run opens the window straight away
            if res.login_expired:
                connections.set_status(site, "expired", "Session expired during a run - log in again")
            next_queries = []
            for o in res.outcomes:
                d = {"site": site, "query": o.query.keywords, "location": o.query.location, "mode": o.mode,
                     "status": o.status, "reported": o.reported, "collected": o.collected, "pages": o.pages,
                     "error": o.error}
                if o.status == "limit" and round_ < MAX_SPLIT_ROUNDS:
                    children = planner.split_query(o.query)
                    if children:
                        d["status"] = "split"
                        next_queries += children
                outcomes.append(d)
            if next_queries:
                events.emit({"type": "agent", "site": site,
                             "message": f"{len(next_queries)} narrower searches (results hit the site limit)"}, run_id=run_id)
            queries = next_queries
    except llm.BudgetExceeded as e:
        errors.append(f"{site}: {e}")
    except Exception as e:  # one site failing never stops the others
        log.exception("site agent %s failed", site)
        errors.append(f"{site}: {e}")
        outcomes.append({"site": site, "query": "(agent)", "location": "", "mode": "guest", "status": "failed",
                         "reported": None, "collected": 0, "pages": 0, "error": str(e)[:300]})
    progress.source(run_id, site, done=True, jobs=len(jobs))
    events.emit({"type": "site_done", "site": site, "jobs": len(jobs),
                 "message": f"{SITES[site].label}: {len(jobs)} jobs"}, run_id=run_id)
    return {"raw_jobs": [j.model_dump(mode="json") for j in jobs], "outcomes": outcomes, "errors": errors}


async def feeds(payload: dict) -> dict:
    run_id = payload["run_id"]
    fp = _fp(payload["filter_profile_id"])
    sources = set(fp.sources or [])
    jobs: list[RawJob] = []
    outcomes, errors = [], []

    with db.session_scope() as s:
        n_ats = len([c for c in s.exec(select(m.TargetCompany).where(m.TargetCompany.active == True)).all()  # noqa: E712
                     if c.ats in F.ATS]) if "ats_feeds" in sources else 0
    planned = n_ats + (len(payload.get("feed_queries", [])) if "arbeitsagentur" in sources else 0) \
        + len({"arbeitnow", "remoteok"} & sources)
    progress.source(run_id, "feeds", phase="searching", searched=0, searches=planned, reads_pages=False)

    def rec(src, query, n, status="ok", reported=None, err=""):
        outcomes.append({"site": src, "query": query, "location": "", "mode": "feed", "status": status,
                         "reported": reported, "collected": n, "pages": 1, "error": err[:300]})
        progress.source(run_id, "feeds", searched=len(outcomes))

    async with F._client(transport=FEED_TRANSPORT) as client:
        if "ats_feeds" in sources:
            with db.session_scope() as s:
                companies = [c for c in s.exec(select(m.TargetCompany).where(m.TargetCompany.active == True)).all()  # noqa: E712
                             if c.ats in F.ATS]
            for c in companies:
                try:
                    got = await F.ATS[c.ats](c.board_id, c.name, fp.days, client)
                    jobs += got
                    rec("ats_feeds", f"{c.name} ({c.ats})", len(got))
                except Exception as e:
                    rec("ats_feeds", f"{c.name} ({c.ats})", 0, "failed", err=str(e))
        if "arbeitsagentur" in sources:
            ba_jobs = []
            for fq in payload.get("feed_queries", []):
                try:
                    got, total = await F.arbeitsagentur(fq["keywords"], fq.get("location", ""), fp.days, client,
                                                        angebotsart=fq.get("angebotsart"), part_time=fq.get("part_time", False))
                    ba_jobs += got
                    rec("arbeitsagentur", fq["keywords"], len(got), reported=total)
                except Exception as e:
                    rec("arbeitsagentur", fq["keywords"], 0, "failed", err=str(e))
            known = known_url_keys()
            from ..textutil import normalize_url
            fresh = [j for j in ba_jobs if normalize_url(j.url) not in known and not prefilter_reason(j, fp)]
            try:
                await F.arbeitsagentur_details(fresh, client)
            except Exception as e:
                errors.append(f"arbeitsagentur details: {e}")
            jobs += ba_jobs
        for src, fn in (("arbeitnow", F.arbeitnow), ("remoteok", F.remoteok)):
            if src in sources:
                try:
                    got = await fn(fp.days, client)
                    jobs += got
                    rec(src, "latest", len(got))
                except Exception as e:
                    rec(src, "latest", 0, "failed", err=str(e))
    progress.source(run_id, "feeds", done=True, jobs=len(jobs))
    events.emit({"type": "site_done", "site": "feeds", "jobs": len(jobs),
                 "message": f"Job feeds: {len(jobs)} jobs"}, run_id=run_id)
    return {"raw_jobs": [j.model_dump(mode="json") for j in jobs], "outcomes": outcomes, "errors": errors}


def clean_dedupe(state: SearchState) -> dict:
    fp = _fp(state["filter_profile_id"])
    raws = [RawJob.model_validate(r) for r in state.get("raw_jobs", [])]
    progress.post(state["run_id"], "storing", 0, len(raws))
    counts = ingest(raws, state["run_id"], fp)
    new_ids = counts.pop("new_ids")
    with db.session_scope() as s:
        for o in state.get("outcomes", []):
            s.add(m.SearchStat(run_id=state["run_id"], **o))
    _emit(state, f"{counts['found']} found, {counts['new']} new, {counts['duplicates']} duplicates, "
                 f"{counts['excluded']} excluded", stage="clean")
    return {"counts": counts, "new_ids": new_ids}


def _ticker(run_id: int, stage: str, total: int):
    """Counts finished jobs for the progress bar (called from the worker threads)."""
    import threading
    lock, n = threading.Lock(), [0]

    def tick():
        with lock:
            n[0] += 1
            progress.post(run_id, stage, n[0], total)
    return tick


def _parallel(fn, items):
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        return list(ex.map(fn, items))


def classify(state: SearchState) -> dict:
    fp = _fp(state["filter_profile_id"])
    run_id = state["run_id"]
    ids = state.get("new_ids", [])

    progress.post(run_id, "sorting", 0, len(ids))
    tick = _ticker(run_id, "sorting", len(ids))

    def one(jid):
        try:
            return _classify_one(jid)
        finally:
            tick()

    def _classify_one(jid):
        llm.set_current_run(run_id)
        with db.session_scope() as s:
            job = s.get(m.Job, jid)
        try:
            c = classify_job(job)
        except llm.BudgetExceeded as e:
            return ("budget", str(e))
        except Exception as e:
            return ("error", f"classify {jid}: {e}")
        with db.session_scope() as s:
            job = s.get(m.Job, jid)
            apply_classification(job, c, fp)
            job.updated_at = datetime.now().astimezone()
            s.add(job)
            return ("ok", job.status)

    res = _parallel(one, ids)
    counts = dict(state.get("counts", {}))
    counts["classified"] = sum(1 for r in res if r[0] == "ok")
    counts["full_time_excluded"] = 0
    with db.session_scope() as s:
        for jid in ids:
            j = s.get(m.Job, jid)
            if j and j.exclude_reason in ("Full-time", "Senior role (full-time)"):
                counts["full_time_excluded"] += 1
    counts["needs_review"] = sum(1 for r in res if r == ("ok", m.JobStatus.NEEDS_REVIEW))
    errors = [r[1] for r in res if r[0] in ("error", "budget")]
    _emit(state, f"Classified {counts['classified']} jobs ({counts['full_time_excluded']} full-time excluded, "
                 f"{counts['needs_review']} need review)", stage="classify")
    return {"counts": counts, "errors": errors[:20]}


def score(state: SearchState) -> dict:
    run_id = state["run_id"]
    main = get_main()
    counts = dict(state.get("counts", {}))
    if not main:
        _emit(state, "No main CV yet - upload it on the My CV screen, then press Re-score", stage="score", level="warn")
        counts["scored"] = 0
        return {"counts": counts, "errors": ["No main CV - jobs were not scored"]}
    profile, vid = main
    with db.session_scope() as s:
        ids = [j.id for j in s.exec(select(m.Job).where(m.Job.first_seen_run == run_id,
                                                         m.Job.status.in_([m.JobStatus.NEW, m.JobStatus.NEEDS_REVIEW]))).all()]

    progress.post(run_id, "scoring", 0, len(ids))
    tick = _ticker(run_id, "scoring", len(ids))

    def one(jid):
        try:
            return _score_one(jid)
        finally:
            tick()

    def _score_one(jid):
        llm.set_current_run(run_id)
        with db.session_scope() as s:
            job = s.get(m.Job, jid)
        try:
            store_score(jid, score_job(job, profile), vid)
            return "ok"
        except llm.BudgetExceeded as e:
            return f"budget: {e}"
        except Exception as e:
            return f"score {jid}: {e}"

    res = _parallel(one, ids)
    counts["scored"] = sum(1 for r in res if r == "ok")
    _emit(state, f"Scored {counts['scored']} jobs", stage="score")
    return {"counts": counts, "errors": [r for r in res if r != "ok"][:20]}


def export(state: SearchState) -> dict:
    run_id = state["run_id"]
    path = export_run(run_id)
    counts = dict(state.get("counts", {}))
    with db.session_scope() as s:
        rows = s.exec(select(m.Job).where(m.Job.first_seen_run == run_id)).all()
        scores = {sc.job_id: sc for sc in s.exec(select(m.Score)).all()}
    by_role, by_source, by_verdict = {}, {}, {}
    for j in rows:
        if j.status in (m.JobStatus.SCORED, m.JobStatus.NEEDS_REVIEW, m.JobStatus.NEW):
            by_role[j.role_category or "?"] = by_role.get(j.role_category or "?", 0) + 1
            for src in j.sources:
                by_source[src] = by_source.get(src, 0) + 1
            if j.id in scores:
                v = scores[j.id].verdict
                by_verdict[v] = by_verdict.get(v, 0) + 1
    counts.update({"by_role": by_role, "by_source": by_source, "by_verdict": by_verdict})
    cost = llm.cost_tracker.run_cost.get(run_id, 0.0)
    _update_run(run_id, counts=counts, errors=state.get("errors", []), status="done",
                finished_at=datetime.now().astimezone(), cost_usd=round(cost, 4))
    progress.finish(run_id, "done")
    _emit(state, f"Done. Saved {path.name}", stage="done", file=str(path))
    events.emit({"type": "run_done", "kind": "search", "message": "Search finished"}, run_id=run_id)
    return {"export_path": str(path), "counts": counts}


# ------------------------------------------------------------------ graph ---
def build_graph(checkpointer=None):
    g = StateGraph(SearchState)
    g.add_node("plan_queries", plan_queries)
    g.add_node("site_agent", site_agent)
    g.add_node("feeds", feeds)
    g.add_node("web_search", web_search)
    g.add_node("clean_dedupe", clean_dedupe)
    g.add_node("classify", classify)
    g.add_node("score", score)
    g.add_node("export", export)
    g.add_edge(START, "plan_queries")
    g.add_conditional_edges("plan_queries", fan_out, ["site_agent", "feeds", "web_search", "clean_dedupe"])
    g.add_edge("site_agent", "clean_dedupe")
    g.add_edge("feeds", "clean_dedupe")
    g.add_edge("web_search", "clean_dedupe")
    g.add_edge("clean_dedupe", "classify")
    g.add_edge("classify", "score")
    g.add_edge("score", "export")
    g.add_edge("export", END)
    return g.compile(checkpointer=checkpointer)


async def run_search(filter_profile_id: int, run_id: Optional[int] = None, resume: bool = False) -> dict:
    """Start (or resume) a search run. Returns the final state."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    if run_id is None:
        with db.session_scope() as s:
            r = m.Run(kind="search", filter_profile_id=filter_profile_id, status="running")
            s.add(r)
            s.flush()
            run_id = r.id
            r.thread_id = f"search-{run_id}"
            s.add(r)
    thread = {"configurable": {"thread_id": f"search-{run_id}"}}
    llm.set_current_run(run_id)
    events.emit({"type": "run_started", "kind": "search", "message": "Search started"}, run_id=run_id)
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
            graph = build_graph(saver)
            inp: Any = None if resume else {"run_id": run_id, "filter_profile_id": filter_profile_id}
            final = await graph.ainvoke(inp, thread)
        final["run_id"] = run_id
        return final
    except asyncio.CancelledError:
        progress.finish(run_id, "cancelled")
        raise
    except Exception as e:
        progress.finish(run_id, "failed")
        log.exception("search run failed")
        _update_run(run_id, status="failed", finished_at=datetime.now().astimezone(), errors=[str(e)[:500]])
        events.emit({"type": "run_failed", "kind": "search", "message": f"Search failed: {e}"}, run_id=run_id)
        raise
