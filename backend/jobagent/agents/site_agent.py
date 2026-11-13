"""One agent per job site.

For every search it works through the fallback order:

1. try as guest
2. login wall / blocked / 0 results where there should be results ->
   retry the same search with the saved login profile (only if connected)
3. captcha / "verify you are human" check ->
   with a login: use the login window; without one: switch to a visible browser
   window (bot checks block headless browsers), wait for you to pass the check
   (noticed automatically, or you press "Solved"), then carry on in that window.
   If nobody passes it, the site's remaining searches are skipped (not hammered)
   and retried on the next run.

It reads every result page up to the configured limit, fetches the full job
description for new jobs, and records per-search coverage (reported vs
collected) so the planner can split searches that hit the site's limit.
"""
from __future__ import annotations

import asyncio
import re
import time
import logging
import random
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..browser.reader import PageReader, Snapshot, detect_block
from ..config import settings
from ..schemas import Query, RawJob
from ..textutil import normalize_url
from .extract import extracted_to_raw, llm_extract, main_text
from .sites import SITES, SiteSpec

log = logging.getLogger(__name__)

MANUAL_NOTE = ("{site} blocks automated browsers - not searched by the app's browser. "
               "It is collected in a tab of your own Chrome instead (Search screen, under Sources).")
AUTO_CHECK_SECONDS = 20          # how long an automatic check may take before you are asked
BLOCK_BACKOFF = [45, 120]        # seconds to wait after a refused page before trying it again
SITE_MIN_DELAY = {"indeed": 8.0, "glassdoor": 6.0, "linkedin": 4.0}   # seconds between two searches

Emit = Callable[[dict], None]
CaptchaHandler = Callable[[str, str], Awaitable[bool]]   # (site, url) -> solved?


def _ray_id(snap) -> str:
    """Cloudflare's request id on a block page - a new one means the page was loaded again."""
    import re
    if not snap:
        return ""
    m = re.search(r"Ray[- ]?ID\D{0,60}?\b([0-9a-f]{16})\b", snap.text or "", re.I)
    return m.group(1) if m else ""


@dataclass
class SearchOutcome:
    query: Query
    mode: str = "guest"
    status: str = "ok"            # ok / login_needed / login_expired / captcha / failed / limit
    reported: Optional[int] = None
    collected: int = 0
    pages: int = 0
    error: str = ""


@dataclass
class SiteResult:
    site: str
    jobs: list[RawJob] = field(default_factory=list)
    outcomes: list[SearchOutcome] = field(default_factory=list)
    login_expired: bool = False


class SiteAgent:
    on_progress = staticmethod(lambda **kw: None)     # progress bar: searched / pages read (set per run)

    def __init__(self, site: str, *, connection_status: str = "not_connected",
                 known_url_keys: Optional[set] = None, emit: Optional[Emit] = None,
                 on_captcha: Optional[CaptchaHandler] = None,
                 prefilter: Optional[Callable[[RawJob], Optional[str]]] = None,
                 reader_factory: Callable[[str, str], PageReader] = None,
                 max_pages: Optional[int] = None, start_mode: str = "guest",
                 on_progress: Optional[Callable[..., None]] = None):
        self.spec: SiteSpec = SITES[site]
        self.site = site
        self.can_login = self.spec.supports_login and connection_status == "connected"
        self.known = known_url_keys or set()
        self.emit = emit or (lambda e: None)
        if on_progress:
            self.on_progress = on_progress
        self.on_captcha = on_captcha
        self.prefilter = prefilter or (lambda j: None)
        self.reader_factory = reader_factory or (lambda s, mode: PageReader(
            s, mode, headless=settings.headless_guest if mode == "guest" else False))
        self.max_pages = max_pages or max(settings.max_pages_per_search, self.spec.max_pages or 0)
        self._readers: dict[str, PageReader] = {}
        self.start_mode = start_mode if start_mode in ("guest", "window", "manual") else "guest"
        self.refuses_automation = False       # the site blocks automated browsers even after a person passes its check
        self.human_checks_passed = 0
        self.prefer_login = False             # guest was challenged -> use the login for the other searches
        self.human_check_given_up = False     # nobody passed the check -> skip the rest of this site
        self.blocked_in_row = 0
        self.blocked_given_up = False         # the site refused several searches in a row -> stop for this run
        self.human_checks = 0

    # ---------------------------------------------------------- readers ---
    async def reader(self, mode: str) -> PageReader:
        if mode not in self._readers:
            r = self.reader_factory(self.site, mode)
            await r.__aenter__()
            self._readers[mode] = r
        return self._readers[mode]

    async def close(self) -> None:
        for r in self._readers.values():
            try:
                await r.__aexit__(None, None, None)
            except Exception:  # pragma: no cover
                pass
        self._readers.clear()

    def _log(self, msg: str, **kw) -> None:
        self.emit({"type": "agent", "site": self.site, "message": msg, **kw})

    # ------------------------------------------------------------- core ---
    async def _read_page(self, q: Query, page: int, mode: str) -> tuple[Optional[str], Snapshot, list[RawJob], Optional[int]]:
        r = await self.reader(mode)
        url = self.spec.search_url(self.spec, q, page, mode)
        snap = await r.goto(url)
        block = detect_block(snap)
        if block == "error":
            await r.pause()
            snap = await r.goto(url)
            block = detect_block(snap)
        if block == "captcha" and mode != "guest":
            # In a real browser window many checks pass by themselves after a few seconds.
            snap, block = await self._settle_check(r, snap)
        jobs, total = [], None
        if block is None:
            jobs, total = self.spec.parse_results(self.spec, snap, q)
            if not jobs and len(snap.text.strip()) > 200:
                # Layout changed or unknown site: let Claude read the page.
                ext = await asyncio.to_thread(llm_extract, snap, self.site)
                if ext.page_kind in ("login_wall", "captcha"):
                    block = ext.page_kind
                else:
                    jobs = extracted_to_raw(self.site, ext.jobs, q.keywords)
                    total = ext.total_results_shown
        if block:
            await r.save_debug(snap, block, note=f"query={q.keywords!r} loc={q.location!r} page={page + 1}")
        return block, snap, jobs, total

    async def _settle_check(self, r: PageReader, snap: Snapshot) -> tuple[Snapshot, Optional[str]]:
        """Give an automatic check (e.g. Cloudflare 'Just a moment') time to finish on its own."""
        waited, limit = 0.0, AUTO_CHECK_SECONDS if settings.page_delay_seconds > 0 else 0.4
        while waited < limit:
            await asyncio.sleep(0.2 if limit < 1 else 2)
            waited += 0.2 if limit < 1 else 2
            cur = await r.current()
            if cur is not None and cur.text.strip() and detect_block(cur) is None:
                self._log("automatic check passed")
                fresh = await r.goto(cur.final_url, wait_ms=0)       # full snapshot of the real page
                return fresh, detect_block(fresh)
        return snap, "captcha"

    async def _wait_for_human(self, r: PageReader, url: str) -> bool:
        """Ask you to pass the check; True as soon as the window shows a normal page or you press Solved."""
        self.human_checks += 1
        await r.show_for_user()
        self._log(f"{self.spec.label} wants a human check - please solve it in the browser window that opened",
                  level="warn", url=url)
        ray0 = _ray_id(await r.current())
        ask = asyncio.ensure_future(self.on_captcha(self.site, url))
        poll = min(3.0, max(0.2, settings.page_delay_seconds))
        try:
            while not ask.done():
                await asyncio.sleep(poll)
                snap = await r.current()
                if snap is None or not snap.text.strip():
                    continue
                if detect_block(snap) is None:
                    self._log("check passed - continuing")
                    return True
                ray = _ray_id(snap)
                if ray0 and ray and ray != ray0:
                    # You passed the box, the site loaded again and refused anyway: it blocks automated
                    # browsers whatever a person does, so stop asking.
                    self.refuses_automation = True
                    self._log(f"{self.spec.label} refused the page again right after the check was passed - "
                              "it blocks automated browsers", level="warn")
                    return False
            return bool(ask.result())
        finally:
            if not ask.done():
                ask.cancel()
            self.emit({"type": "captcha_done", "site": self.site})

    async def _between_searches(self) -> None:
        """Human pacing between two searches on the same site (sites block bursts of requests)."""
        if settings.page_delay_seconds <= 0:
            return
        d = max(settings.page_delay_seconds, SITE_MIN_DELAY.get(self.site, 0))
        await asyncio.sleep(d * random.uniform(1.0, 1.6))

    async def run_query(self, q: Query) -> tuple[SearchOutcome, list[RawJob]]:
        out = SearchOutcome(query=q)
        if self.start_mode == "manual" or (self.human_check_given_up and self.refuses_automation):
            out.status, out.error = "manual", MANUAL_NOTE.format(site=self.spec.label)
            return out, []
        if self.human_check_given_up:
            out.status, out.error = "captcha", f"skipped - {self.spec.label} asked for a human check that wasn't solved"
            return out, []
        if self.blocked_given_up:
            out.status, out.error = "blocked", f"skipped - {self.spec.label} refused several searches in a row"
            return out, []
        mode = "login" if (self.prefer_login and self.can_login) else self.start_mode
        found: dict[str, RawJob] = {}
        page = 0
        switched = False
        asked = 0
        backoffs = 0
        while page < self.max_pages:
            block, snap, jobs, total = await self._read_page(q, page, mode)

            # Guest got nothing on page 0 although the site normally has results -> treat as soft block
            soft_empty = (block is None and page == 0 and not jobs and mode == "guest"
                          and self.can_login and self.spec.parse_results.__name__ != "no_parse")

            if (block in ("login_wall", "blocked") or soft_empty) and mode == "guest" and self.can_login and not switched:
                self._log(f"'{q.keywords}' in {q.location}: guest failed ({block or 'empty'}), retrying with login")
                mode, switched = "login", True
                self.prefer_login = block == "captcha" or self.prefer_login
                continue
            if block == "login_wall" and mode == "guest":
                out.status = "login_needed"
                break

            if block == "login_wall" and mode == "login":
                out.status = "login_expired"
                self._log("login session expired - please log in again on the Connections screen", level="warn")
                break

            if block == "captcha":
                if mode == "guest" and self.can_login and not switched:
                    self._log(f"'{q.keywords}' in {q.location}: human check as guest - using your login")
                    mode, switched, self.prefer_login = "login", True, True     # stay logged in for the rest
                    continue
                if mode == "guest" and self.on_captcha and settings.headless_guest:
                    # Headless browsers get the check on every page: use a visible window from now on.
                    self._log(f"'{q.keywords}' in {q.location}: human check as guest - opening a browser window")
                    mode = self.start_mode = "window"
                    continue
                if asked >= 1 and self.human_checks_passed:
                    # the check came straight back after it was passed: the site blocks automation
                    self.refuses_automation = self.human_check_given_up = True
                    self._log(f"{self.spec.label} shows the check again right after it was passed - "
                              "it blocks automated browsers", level="warn")
                    out.status, out.error = "manual", MANUAL_NOTE.format(site=self.spec.label)
                    break
                solved = False
                if self.on_captcha and (mode != "guest" or not settings.headless_guest) and asked < 2:
                    asked += 1
                    solved = await self._wait_for_human(await self.reader(mode), snap.final_url)
                    if solved:
                        self.human_checks_passed += 1
                    elif self.refuses_automation:
                        self.human_check_given_up = True
                        out.status, out.error = "manual", MANUAL_NOTE.format(site=self.spec.label)
                        break
                    if not solved:
                        self.human_check_given_up = True
                        self._log(f"check not solved - skipping the other {self.spec.label} searches this run "
                                  "(they are retried next run)", level="warn")
                if solved:
                    continue
                if asked >= 2:
                    self.human_check_given_up = True       # "Solved" twice but the check is still there
                out.status, out.error = "captcha", snap.final_url
                break

            if block == "blocked":
                # 403 / 429 without a check: the site wants us to slow down. Wait, then try the same page again.
                if backoffs < len(BLOCK_BACKOFF):
                    wait = BLOCK_BACKOFF[backoffs] if settings.page_delay_seconds > 0 else 0
                    backoffs += 1
                    self._log(f"'{q.keywords}': {self.spec.label} refused the page (HTTP {snap.status}) - "
                              f"waiting {wait}s and trying again", level="warn")
                    await asyncio.sleep(wait)
                    continue
                self.blocked_in_row += 1
                out.status = "partial" if found else "blocked"
                out.error = f"HTTP {snap.status}" + (f" after page {page}" if found else "")
                if self.blocked_in_row >= 2:
                    self.blocked_given_up = True
                    self._log(f"{self.spec.label} keeps refusing - stopping it for this run "
                              "(the rest is retried next run)", level="warn")
                break

            if block == "error":
                out.status = "partial" if found else "failed"
                out.error = snap.error or f"HTTP {snap.status}"
                break

            self.blocked_in_row = 0
            if page == 0 and total is not None:
                out.reported = total
            new_on_page = 0
            for j in jobs:
                k = normalize_url(j.url)
                if k and k not in found:
                    j.query = q.keywords
                    found[k] = j
                    new_on_page += 1
            out.pages = page + 1
            self._log(f"'{q.keywords}' in {q.location}: page {page + 1}, {len(found)} jobs", page=page + 1)
            if len(jobs) < max(1, int(self.spec.size(mode) * 0.5)) or new_on_page == 0:
                break
            page += 1
            await (await self.reader(mode)).pause()
        else:
            # Read the max number of pages and every page was full: more results exist.
            out.status = "limit"

        out.mode = mode
        out.collected = len(found)
        if out.status == "ok" and out.reported and out.reported >= self.spec.result_limit * 0.9:
            out.status = "limit"
        for j in found.values():
            j.job_type_hint = j.job_type_hint or ""
            j.mode = mode
        return out, list(found.values())

    def detail_order(self, jobs: list[RawJob]) -> list[RawJob]:
        """The job pages worth reading first: titles that name your roles' skills and a student job type."""
        from ..vocab import ALL_TERMS, find_terms
        student = re.compile(r"werkstudent|working student|intern|praktik|thesis|abschlussarbeit|trainee", re.I)

        def rank(j: RawJob) -> float:
            t = j.title or ""
            return len(find_terms(t, ALL_TERMS)) + (2 if student.search(t) else 0)
        return sorted(jobs, key=rank, reverse=True)       # stable: same rank keeps the search order

    async def fetch_details(self, jobs: list[RawJob]) -> None:
        todo = [j for j in self.detail_order(jobs) if not j.description]
        cap = max(0, settings.max_details_per_site)
        if cap and len(todo) > cap:
            self._log(f"reading the {cap} most relevant of {len(todo)} job pages this run "
                      f"(the other {len(todo) - cap} keep their short text; MAX_DETAILS_PER_SITE in Settings)")
            todo = todo[:cap]
        t0, done, backoffs = time.monotonic(), 0, 0
        i = 0
        self.on_progress(phase="reading", pages_done=0, pages_total=len(todo))
        while i < len(todo):
            j = todo[i]
            mode = j.mode if j.mode in ("guest", "login", "window") else "guest"
            r = await self.reader(mode)
            url = self.spec.detail_url(self.spec, j, mode)
            snap = await r.goto(url)
            block = detect_block(snap)
            if block == "captcha" and self.start_mode == "window" and mode == "guest":
                r = await self.reader("window")
                snap = await r.goto(url)
                block = detect_block(snap)
            if block == "captcha":
                self._log("human check while reading job pages - the remaining descriptions are skipped", level="warn")
                break
            if block == "blocked" and snap.status in (429, 403, 999):
                # too many requests: wait, then the same page again (twice at most in a row)
                if backoffs < len(BLOCK_BACKOFF):
                    wait = BLOCK_BACKOFF[backoffs] if settings.page_delay_seconds > 0 else 0
                    backoffs += 1
                    self._log(f"{self.spec.label} asked to slow down (HTTP {snap.status}) - waiting {wait}s", level="warn")
                    await asyncio.sleep(wait)
                    continue
                self._log(f"{self.spec.label} keeps refusing job pages - the remaining {len(todo) - i} keep their short text",
                          level="warn")
                break
            backoffs = 0
            if block in ("login_wall", "blocked") and mode == "guest" and self.can_login:
                r = await self.reader("login")
                snap = await r.goto(j.url)
                block = detect_block(snap)
            if block is None:
                j.description = main_text(snap, self.spec.detail_selectors)
                if self.spec.parse_detail_extra:
                    for k, v in self.spec.parse_detail_extra(snap).items():
                        if v and not getattr(j, k):
                            setattr(j, k, v)
            done += 1
            i += 1
            self.on_progress(phase="reading", pages_done=i, pages_total=len(todo))
            if done % 25 == 0 and i < len(todo):
                per = (time.monotonic() - t0) / done
                mins = round(per * (len(todo) - i) / 60)
                self._log(f"read {i} of {len(todo)} job pages, about {mins} min left", done=i, total=len(todo))
            await r.pause()
        if todo:
            self._log(f"read {i} of {len(todo)} job pages")

    async def run(self, queries: list[Query]) -> SiteResult:
        res = SiteResult(site=self.site)
        all_jobs: dict[str, RawJob] = {}
        fresh = 0                                   # new jobs that pass the quick pre-filter (their pages get read)
        self.on_progress(phase="searching", searched=0, searches=len(queries), new_seen=0)
        try:
            for i, q in enumerate(queries):
                if i and not (self.human_check_given_up or self.blocked_given_up or self.start_mode == "manual"):
                    await self._between_searches()
                outcome, jobs = await self.run_query(q)
                res.outcomes.append(outcome)
                if outcome.status == "login_expired":
                    res.login_expired = True
                    self.can_login = False
                for j in jobs:
                    j.place = j.place or q.place
                    k = normalize_url(j.url)
                    if k not in all_jobs:
                        all_jobs[k] = j
                        if k not in self.known and not j.description and not self.prefilter(j):
                            fresh += 1
                self.on_progress(phase="searching", searched=i + 1, searches=len(queries), new_seen=fresh)
            # Only new jobs that survive the quick pre-filter get their description fetched.
            to_fetch = []
            for k, j in all_jobs.items():
                if k in self.known:
                    continue
                reason = self.prefilter(j)
                if reason:
                    j.prefilter_reason = reason
                    continue
                to_fetch.append(j)
            self._log(f"fetching {len(to_fetch)} job descriptions")
            await self.fetch_details(to_fetch)
            res.jobs = list(all_jobs.values())
        finally:
            await self.close()
        return res
