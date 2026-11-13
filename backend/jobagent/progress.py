"""Progress and time left for a search run: one line per source, and the whole run.

Every source reports where it is (searching: search i of n; reading job pages: page i of n). The time left
comes from how fast that source has been so far. While a site is still searching, the job pages it will read
afterwards are estimated from the new jobs it has found so far (at most MAX_DETAILS_PER_SITE). The run's time
left is the slowest source (they run at the same time) plus storing, sorting and scoring the new jobs.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from .config import settings

_lock = threading.RLock()
RUNS: dict[int, dict] = {}
LABELS = {"linkedin": "LinkedIn", "stepstone": "StepStone", "xing": "XING", "wellfound": "Wellfound",
          "glassdoor": "Glassdoor", "indeed": "Indeed", "feeds": "Job feeds", "websearch": "Other job sites"}
POST_SECONDS_PER_JOB = 0.4          # store + sort + score one new job (measured on a laptop, offline rules)


def _page_seconds() -> float:
    return settings.page_delay_seconds * 1.15 + 2.5


def _blank(key: str, now: float, searches: int = 0, reads_pages: bool = True) -> dict:
    return {"label": LABELS.get(key, key), "phase": "waiting", "searched": 0, "searches": searches, "pages_done": 0,
            "pages_total": 0, "new_seen": 0, "jobs": 0, "t_search": None, "search_secs": 0.0, "t_pages": None,
            "done": False, "reads_pages": reads_pages, "updated": now}


def _run(run_id: int) -> dict:
    """The run's record (made on first use, e.g. when a run is resumed after a restart)."""
    return RUNS.setdefault(run_id, {"started": time.time(), "stage": "searching", "post": None, "sources": {}})


def start(run_id: int, sources: dict[str, int], no_pages: tuple = ()) -> None:
    """sources: source -> number of planned searches; no_pages: sources that read no job pages afterwards."""
    now = time.time()
    with _lock:
        RUNS[run_id] = {"started": now, "stage": "searching", "post": None,
                        "sources": {k: _blank(k, now, n, k not in no_pages) for k, n in sources.items()}}
    _push(run_id, force=True)


def source(run_id: Optional[int], key: str, **kw) -> None:
    """A source reports where it is: phase searching (searched, searches, new_seen) / reading (pages_done,
    pages_total) / done (jobs)."""
    if not run_id:
        return
    now = time.time()
    with _lock:
        s = _run(run_id)["sources"].setdefault(key, _blank(key, now))
        phase = kw.get("phase")
        changed = bool(phase) and phase != s["phase"]
        if phase == "searching" and s["phase"] != "searching":
            s["t_search"] = now - s["search_secs"]           # time spent searching so far counts, reading does not
        if phase == "reading" and s["phase"] == "searching" and s["t_search"]:
            s["search_secs"] = now - s["t_search"]
        if phase == "reading" and (s["phase"] != "reading" or not kw.get("pages_done")):
            s["t_pages"] = now
        s.update({k: v for k, v in kw.items() if v is not None})
        s["updated"] = now
        if s.get("done"):
            s["phase"] = "done"
    _push(run_id, force=bool(kw.get("done")) or changed)


def post(run_id: Optional[int], stage: str, done: int = 0, total: int = 0) -> None:
    """After the sources: storing / sorting / scoring the new jobs."""
    if not run_id:
        return
    with _lock:
        r = _run(run_id)
        p = r.get("post") or {}
        if p.get("stage") != stage:
            p = {"stage": stage, "t0": time.time()}
        p.update(done=done, total=total)
        r["post"], r["stage"] = p, stage
        for s in r["sources"].values():
            s["done"], s["phase"] = True, "done"
    _push(run_id, force=done in (0, total))


def finish(run_id: Optional[int], status: str = "done") -> None:
    if not run_id or run_id not in RUNS or RUNS[run_id]["stage"] in ("done", "failed", "cancelled"):
        return
    with _lock:
        RUNS[run_id]["stage"] = status
        for s in RUNS[run_id]["sources"].values():
            s["done"], s["phase"] = True, "done"
    _push(run_id, force=True)


def _eta_source(s: dict, now: float) -> Optional[float]:
    if s["done"]:
        return 0.0
    cap = max(0, settings.max_details_per_site) or 10 ** 6
    left = 0.0
    if s["phase"] in ("waiting", "searching"):
        if not (s["searches"] and s["searched"] and s["t_search"]):
            return None                                    # no speed known yet
        per = (now - s["t_search"]) / s["searched"]
        left += per * max(0, s["searches"] - s["searched"])
        projected_new = s["new_seen"] * s["searches"] / s["searched"]
        if s["reads_pages"]:
            left += min(cap, projected_new) * _page_seconds()
        return left
    if s["phase"] == "saving":
        return s["jobs"] * POST_SECONDS_PER_JOB
    if s["phase"] == "reading":
        done, total = s["pages_done"], s["pages_total"]
        if done and s["t_pages"]:
            per = (now - s["t_pages"]) / done
        else:
            per = _page_seconds()
        return per * max(0, total - done)
    return None


def snapshot(run_id: int) -> Optional[dict]:
    with _lock:
        return _snapshot(run_id)


def _snapshot(run_id: int) -> Optional[dict]:
    r = RUNS.get(run_id)
    if not r:
        return None
    now = time.time()
    rows, etas = [], []
    for k, s in r["sources"].items():
        eta = _eta_source(s, now)
        if eta is not None:
            etas.append(eta)
        if s["phase"] == "reading" and s["pages_total"]:
            frac = 0.5 + 0.5 * s["pages_done"] / s["pages_total"]
            what = f"reading job pages {s['pages_done']} of {s['pages_total']}"
        elif s["phase"] == "saving":
            frac, what = 0.97, f"saving {s['jobs']} jobs to your job board"
        elif s["phase"] == "done":
            frac, what = 1.0, f"done · {s['jobs']} jobs, on your job board"
        elif s["searches"]:
            frac = min(1.0, s["searched"] / s["searches"]) * (0.5 if s["reads_pages"] else 1.0)
            what = f"search {s['searched']} of {s['searches']}" + (f" · {s['new_seen']} new so far" if s["new_seen"] else "")
        else:
            frac, what = 0.0, "starting"
        rows.append({"key": k, "label": s["label"], "phase": s["phase"], "what": what, "fraction": round(frac, 3),
                     "eta_seconds": None if eta is None else round(eta)})
    p = r.get("post")
    post_left = 0.0
    if p and p.get("total"):
        rate = (now - p["t0"]) / p["done"] if p.get("done") else 0
        rate = rate or POST_SECONDS_PER_JOB
        post_left = rate * max(0, p["total"] - p["done"])
    elif r["stage"] == "searching":
        post_left = sum(s["new_seen"] for s in r["sources"].values()) * POST_SECONDS_PER_JOB
    finished = r["stage"] in ("done", "failed", "cancelled")
    if finished:
        total_left = 0.0
    elif r["stage"] == "searching":
        total_left = (max(etas) if etas else None)
        total_left = None if total_left is None else total_left + post_left
    else:
        total_left = post_left
    elapsed = now - r["started"]
    frac = 1.0 if finished else (elapsed / (elapsed + total_left) if total_left is not None and elapsed + total_left > 0
                                             else 0.0)
    return {"run_id": run_id, "stage": r["stage"], "elapsed_seconds": round(elapsed),
            "eta_seconds": None if total_left is None else round(total_left),
            "finishes_at": None if total_left is None else now + total_left,
            "fraction": round(1.0 if finished else min(0.99, frac), 3), "sources": rows,
            "post": p and {"stage": p["stage"], "done": p.get("done", 0), "total": p.get("total", 0)}}


def _push(run_id: int, force: bool = False) -> None:
    """Called after every change. The screen asks for the snapshot itself (GET /api/runs/{id}/progress every
    2 seconds), so nothing is sent here: a progress event every few seconds would push the real log lines out
    of the event history. Tests replace this to watch a run."""
