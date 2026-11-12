"""Web-search agent: finds job postings through Claude's web search instead of opening the sites.

This is how a Claude chat finds jobs: it searches the web and reads the search results. The job
sites are never opened by a program-controlled browser, so bot checks (Indeed's Cloudflare page)
do not apply. One call = one site x one place x one group of roles; Claude runs several searches
in that call and returns the postings it saw.

Two engines, picked automatically:
* "api" - Anthropic API with the web search tool (needs ANTHROPIC_API_KEY; billed per search)
* "cli" - the Claude Code command line (`claude -p`) with its WebSearch tool, using the Claude
          subscription you are logged in with (no API key needed)

Every job must come from a link that really appeared in the search results - anything else is
dropped, so the agent cannot invent postings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable, Optional

from ..config import settings
from ..schemas import RawJob
from ..textutil import normalize_url

log = logging.getLogger(__name__)

# site id -> (label, domains searched)
WEB_SITES: dict[str, tuple[str, list[str]]] = {
    "indeed": ("Indeed", ["de.indeed.com", "indeed.com"]),
    "linkedin": ("LinkedIn", ["linkedin.com"]),
    "stepstone": ("StepStone", ["stepstone.de"]),
    "glassdoor": ("Glassdoor", ["glassdoor.de", "glassdoor.com"]),
    "xing": ("XING", ["xing.com"]),
    "wellfound": ("Wellfound", ["wellfound.com"]),
    "web": ("All job sites", []),              # any board or company career page
}
# Search the most useful sites first, in case the per-run cap is reached
SITE_ORDER = ["indeed", "linkedin", "stepstone", "web", "glassdoor", "xing", "wellfound"]
PER_SITE_API = ["indeed", "glassdoor"]     # sites that block browsers: searched on their own when the API is used
# Links that are a single posting on each site (not a category / search page)
JOB_LINK = {
    "indeed": re.compile(r"indeed\.com/(viewjob|rc/clk|pagead/clk|cmp/[^/]+/jobs|m/viewjob)|[?&]jk=", re.I),
    "linkedin": re.compile(r"linkedin\.com/(jobs/view|jobs-guest/jobs/api/jobPosting)/", re.I),
    "stepstone": re.compile(r"stepstone\.de/stellenangebote--", re.I),
    "glassdoor": re.compile(r"glassdoor\.[a-z.]+/(job-listing|partner/jobListing|Job/.+-job-|.*jobListingId=)", re.I),
    "xing": re.compile(r"xing\.com/jobs/[a-z0-9-]+-\d+", re.I),
    "wellfound": re.compile(r"wellfound\.com/(jobs/\d+|company/[^/]+/jobs/\d+)", re.I),
}
ROLE_GROUPS = [
    ("AI / ML / GenAI", ["AI Engineer", "Machine Learning Engineer", "Machine Learning", "Generative AI", "LLM", "AI ML"]),
    ("Data Science / Analytics", ["Data Scientist", "Data Science", "Data Analyst", "Analytics Engineer"]),
    ("Data Engineering / MLOps", ["Data Engineer", "MLOps", "LLMOps"]),
]
DEFAULT_LOCATIONS = ["Berlin", "Germany", "Remote"]


class WebSearchUnavailable(RuntimeError):
    pass


@dataclass
class WebQuery:
    site: str
    location: str
    group: str
    terms: list[str]
    type_words: list[str]
    days: int

    @property
    def label(self) -> str:
        return f"{WEB_SITES[self.site][0]} · {self.group} · {self.location}"


@dataclass
class WebResult:
    query: WebQuery
    jobs: list[RawJob] = field(default_factory=list)
    seen_links: int = 0
    dropped: int = 0
    searches: int = 0
    cost_usd: float = 0.0
    error: str = ""
    note: str = ""            # why nothing came back, from Claude's own answer


# ------------------------------------------------------------------ planning ---
def plan_web_queries(roles: list[str], job_types: list[str], locations: list[str], days: int,
                     sources: list[str], max_calls: Optional[int] = None) -> list[WebQuery]:
    from ..steps.planner import JT_DE, JT_EN, ROLE_TERMS
    wanted_terms = {t for r in roles for t in ROLE_TERMS.get(r, [r])}
    groups = [(g, [t for t in ts if t in wanted_terms]) for g, ts in ROLE_GROUPS]
    groups = [(g, ts) for g, ts in groups if ts]
    extra = sorted(wanted_terms - {t for _, ts in ROLE_GROUPS for t in ts})
    if extra:
        groups.append(("Other roles", extra))
    locs = [l for l in DEFAULT_LOCATIONS if l in locations] + [l for l in locations if l not in DEFAULT_LOCATIONS]
    # Claude Code's web search ignores site: filters, so it searches all job sites at once ("web");
    # the API's search tool can be limited to one domain, so it also covers the sites that block browsers.
    per_site = PER_SITE_API if (engine() == "api") else []
    sites = [s for s in SITE_ORDER if (s in per_site and s in sources)] + ["web"]
    out: list[WebQuery] = []
    for loc in locs:                         # Berlin for every site first, then Germany, ...
        german = loc in ("Berlin", "Germany")
        words = []
        for jt in job_types:
            ws = (JT_DE.get(jt, []) if german else []) + JT_EN.get(jt, [])
            words += [w for w in ws if w not in words]
        for site in sites:
            if site in ("stepstone", "xing") and loc not in ("Berlin", "Germany", "Remote"):
                continue                     # German job boards
            for g, terms in groups:
                out.append(WebQuery(site=site, location=loc, group=g, terms=terms, type_words=words, days=days))
    cap = settings.websearch_max_calls if max_calls is None else max_calls
    return out[:cap] if cap else out


def build_prompt(q: WebQuery, today: Optional[date] = None) -> str:
    today = today or date.today()
    label, domains = WEB_SITES[q.site]
    where = (f"on {label} only" if domains else
             "on any job board or company career page (Indeed, LinkedIn, StepStone, Glassdoor, XING, arbeitnow, "
             "workingstudentjobs.de, join.com, company /careers pages, ...)")
    place = "remote (work from anywhere in Germany / EU)" if q.location == "Remote" else q.location
    return f"""Find CURRENT job postings {where}.

Looking for: job type {" / ".join(q.type_words)} - roles {", ".join(q.terms)} - location {place}.
Only postings from the last {q.days} days if the date is visible (today is {today.isoformat()}).
Run several different web searches (German and English wording, one role or job-type word per search),
for example:  "{q.type_words[0] if q.type_words else ''} {q.terms[0]}" {place}   - then the other roles and job-type words.

Rules:
- Only list single job postings you actually saw in the search results. Never guess or invent a link.
- "url" must be the exact link of that posting from the search results. If you only saw the job on a
  list/search page, put that page's link in "source_page" and leave "url" empty.
- "summary": 1-4 sentences with the requirements you saw (skills, tools, languages, hours). Do not invent.
- Skip full-time-only and senior positions.

Answer with ONLY a JSON array in a ```json block, one object per job:
{{"title": "", "company": "", "location": "", "url": "", "source_page": "", "posted": "e.g. 2 days ago or 2026-11-03", "job_type": "", "summary": ""}}
Return [] if you found nothing."""


# --------------------------------------------------------------- the engines ---
def engine() -> str:
    """'api', 'cli' or '' (not available)."""
    want = settings.websearch_engine
    if want in ("api", "cli"):
        return want if (want == "api" and settings.anthropic_api_key) or (want == "cli" and claude_cli()) else ""
    if settings.anthropic_api_key:
        return "api"
    return "cli" if claude_cli() else ""


def claude_cli() -> str:
    from ..claude_code import exe
    return exe()


def _extract_json(text: str) -> list[dict]:
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S) or re.search(r"(\[\s*\{.*\}\s*\]|\[\s*\])", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        return [d for d in data if isinstance(d, dict)]
    except ValueError:
        return []


AUTH_ERR = re.compile(r"not logged in|please (run /)?log ?in|/login|authenticat|invalid api key|credit balance|"
                      r"unauthori[sz]ed|\b401\b|usage limit", re.I)
URL_RE = re.compile(r"https?://[^\s\"'<>\]\)\\,]+")


async def _call_api(prompt: str, q: WebQuery) -> tuple[str, set[str], int, float]:
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise WebSearchUnavailable("anthropic package missing") from e
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=180)
    tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": settings.websearch_searches_per_call}
    domains = WEB_SITES[q.site][1]
    if domains:
        tool["allowed_domains"] = domains
    model = settings.model_fast
    msg = await client.messages.create(model=model, max_tokens=4000, tools=[tool],
                                       messages=[{"role": "user", "content": prompt}])
    text, links = "", set()
    for b in msg.content:
        t = getattr(b, "type", "")
        if t == "text":
            text += b.text
        elif t == "web_search_tool_result":
            for r in (b.content if isinstance(b.content, list) else []):
                u = getattr(r, "url", "")
                if u:
                    links.add(u)
    from .. import llm
    pin, pout = llm._price(model)
    searches = getattr(getattr(msg.usage, "server_tool_use", None), "web_search_requests", 0) or 0
    cost = msg.usage.input_tokens / 1e6 * pin + msg.usage.output_tokens / 1e6 * pout + searches * 0.01
    return text, links, searches, cost


async def _call_cli(prompt: str, q: WebQuery) -> tuple[str, set[str], int, float]:
    exe = claude_cli()
    if not exe:
        raise WebSearchUnavailable("Claude Code (claude) is not installed or not on PATH")
    from .. import claude_code as cc
    args = ["-p", "--output-format", "stream-json", "--verbose", "--allowedTools", "WebSearch",
            "--max-turns", str(settings.websearch_searches_per_call * 2 + 10)]
    with tempfile.TemporaryDirectory() as cwd:          # empty folder: no project files are read
        extra, _ = cc.flags(Path(cwd), system=None, no_tools=False, model=cc.cli_model(""))
        cmd = cc.prefix(exe) + args + extra
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=cwd, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=420)
        except asyncio.TimeoutError:
            proc.kill()
            raise WebSearchUnavailable("Claude Code took longer than 7 minutes")
    if proc.returncode != 0 and not out:
        why = (err or b"").decode("utf-8", "replace")[-300:] or "claude exited with an error"
        if AUTH_ERR.search(why):
            from ..claude_code import friendly
            raise WebSearchUnavailable(friendly(why))
        raise RuntimeError(why)
    text, links, searches, cost, last_text = "", set(), 0, 0.0, ""
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        typ = ev.get("type")
        content = (ev.get("message") or {}).get("content") or []
        if typ == "assistant":
            for c in content:
                if c.get("type") == "tool_use" and c.get("name") == "WebSearch":
                    searches += 1
                elif c.get("type") == "text" and "```json" in (c.get("text") or ""):
                    last_text = c.get("text")
        elif typ == "user":
            for c in content:
                if c.get("type") == "tool_result":
                    body = c.get("content")
                    body = body if isinstance(body, str) else " ".join(x.get("text", "") for x in body or [] if isinstance(x, dict))
                    links.update(URL_RE.findall(body))
        elif typ == "assistant_text":
            pass
        elif typ == "result":
            text = ev.get("result") or text
            cost = float(ev.get("total_cost_usd") or 0.0)
            if ev.get("is_error") and AUTH_ERR.search(str(ev.get("result") or "")):
                from ..claude_code import friendly
                raise WebSearchUnavailable(friendly(str(ev.get("result"))))   # logged out: stop all web searches
            if ev.get("is_error") and not text:
                why = str(ev.get("result") or ev.get("subtype") or "Claude Code error")[:300]
                if AUTH_ERR.search(why):
                    from ..claude_code import friendly
                    raise WebSearchUnavailable(friendly(why))  # not logged in: stop all web searches
                raise RuntimeError(why)                        # e.g. error_max_turns: only this search failed
    if not text:                                               # ran out of steps: use its last written answer
        text = last_text
    if text or searches:
        from ..claude_code import note_ok
        note_ok()
    return text, links, searches, cost


# ------------------------------------------------------------- run one query ---
Caller = Callable[[str, WebQuery], Awaitable[tuple[str, set[str], int, float]]]


LIST_TITLE = re.compile(r"search results|suchergebnis|\bjobs in\b|stellenangebote in|^\d+[\d.,]* jobs", re.I)


def _clean(v) -> str:
    """'unknown (date not visible)' and similar -> ''."""
    s = " ".join(str(v or "").split())
    return "" if re.match(r"^(unknown|n/?a|not (visible|stated|shown)|unbekannt|-)\b", s, re.I) else s


def to_jobs(q: WebQuery, items: list[dict], links: set[str]) -> tuple[list[RawJob], int]:
    """Keep only postings whose link (or the page they were seen on) was really in the search results."""
    from ..agents.extract import parse_posted
    seen = {normalize_url(u) for u in links} | {u.rstrip("/") for u in links}
    domains = WEB_SITES[q.site][1]
    jobs, dropped = [], 0
    for it in items:
        title = " ".join(str(it.get("title") or "").split())
        url = str(it.get("url") or "").strip()
        page = str(it.get("source_page") or "").strip()
        ok_url = url and (normalize_url(url) in seen or url.rstrip("/") in seen)
        ok_page = page and (normalize_url(page) in seen or page.rstrip("/") in seen)
        if not title or not (ok_url or ok_page):
            dropped += 1
            continue
        link = url if ok_url else page
        if domains and not any(d in link for d in domains):
            dropped += 1                                   # result from another site in a site search
            continue
        if LIST_TITLE.search(title):
            dropped += 1                                   # a search / category page, not a posting
            continue
        source = q.site
        if q.site == "web":                                # credit known boards to their own source
            source = next((s for s, (_, ds) in WEB_SITES.items() if ds and any(d in link for d in ds)), "web")
        posted = _clean(it.get("posted"))
        jobs.append(RawJob(source=source, title=title[:200], company=_clean(it.get("company"))[:120],
                           location=(_clean(it.get("location")) or q.location)[:120], url=link,
                           posted_text=posted[:40], posted_date=parse_posted(posted),
                           description=str(it.get("summary") or "")[:2000],
                           job_type_hint=_clean(it.get("job_type"))[:60], query=f"{q.group} ({q.location})",
                           mode="web"))
    return jobs, dropped


async def run_query(q: WebQuery, caller: Optional[Caller] = None) -> WebResult:
    res = WebResult(query=q)
    eng = engine() if caller is None else "test"
    if caller is None:
        if not eng:
            raise WebSearchUnavailable("no Anthropic API key and no Claude Code found")
        caller = _call_api if eng == "api" else _call_cli
    prompt = build_prompt(q)
    text, links, res.searches, res.cost_usd = await caller(prompt, q)
    res.seen_links = len(links)
    items = _extract_json(text)
    res.jobs, res.dropped = to_jobs(q, items, links)
    if not items:
        # no job list in the answer: say why (e.g. a usage limit message) instead of a silent 0
        res.note = " ".join((text or "(empty answer)").split())[:220]
    _save_debug(q, prompt, text, links, res)
    return res


def _save_debug(q: WebQuery, prompt: str, text: str, links: set, res: "WebResult") -> None:
    """Keep each web-search answer in data/debug/websearch/ (last 60) so a 0 can be explained."""
    try:
        from datetime import datetime
        from pathlib import Path
        d = Path(settings.data_dir) / "debug" / "websearch"
        d.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", q.label)[:60]
        f = d / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.txt"
        f.write_text(f"QUERY: {q.label}\nSEARCHES: {res.searches}  LINKS SEEN: {len(links)}  KEPT: {len(res.jobs)}  "
                     f"DROPPED: {res.dropped}\n\n--- ANSWER ---\n{text}\n\n--- LINKS ---\n" + "\n".join(sorted(links)),
                     encoding="utf-8")
        for old in sorted(d.glob("*.txt"))[:-60]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


async def run_all(queries: list[WebQuery], caller: Optional[Caller] = None,
                  on_done: Optional[Callable[[WebResult], None]] = None, parallel: Optional[int] = None) -> list[WebResult]:
    sem = asyncio.Semaphore(parallel or settings.websearch_parallel)
    fatal: list[str] = []

    async def one(q):
        async with sem:
            if fatal:
                r = WebResult(query=q, error=f"skipped - {fatal[0]}")
            else:
                try:
                    r = await run_query(q, caller)
                except WebSearchUnavailable as e:          # e.g. not logged in: don't start the rest
                    fatal.append(str(e)[:200])
                    r = WebResult(query=q, error=str(e)[:300])
                except Exception as e:  # one failing search never stops the others
                    log.warning("web search %s failed: %s", q.label, e)
                    r = WebResult(query=q, error=str(e)[:300])
            if on_done:
                on_done(r)
            return r

    results = list(await asyncio.gather(*(one(q) for q in queries)))
    if fatal and not any(r.jobs for r in results):
        raise WebSearchUnavailable(fatal[0])
    return results


# ---------------------------------------------------- descriptions (optional) ---
GONE = re.compile(r"(no longer (available|accepting|active)|nicht mehr (verfügbar|aktiv|online)|"
                  r"stelle (ist )?(bereits )?(besetzt|vergeben)|job (has )?expired|position has been filled)", re.I)
NO_FETCH = ("indeed.", "glassdoor.")      # behind bot checks - keep the search summary


async def fetch_descriptions(jobs: list[RawJob], client=None, limit: int = 80) -> int:
    """Fill in the full text for postings whose page can be read with a plain request (StepStone,
    LinkedIn job pages, company sites). Indeed / Glassdoor keep the summary from the search.
    A posting whose page is gone (404 / 410 / 'no longer available') is removed from `jobs`."""
    import httpx
    from ..agents.extract import main_text
    from ..browser.reader import Snapshot, detect_block
    todo = [j for j in jobs if j.url and not any(d in j.url for d in NO_FETCH)][:limit]
    if not todo:
        return 0
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    done = 0
    try:
        for j in todo:
            try:
                r = await client.get(j.url)
                if r.status_code in (404, 410) or GONE.search(r.text[:30000]):
                    j.prefilter_reason = "posting no longer online"
                    continue
                snap = Snapshot(url=j.url, final_url=str(r.url), status=r.status_code, html=r.text,
                                text=re.sub(r"<[^>]+>", " ", r.text)[:20000])
                if detect_block(snap) is None and r.status_code < 400:
                    text = main_text(snap, ())
                    if len(text) > len(j.description) + 200:
                        j.description = text[:20000]
                        done += 1
            except Exception:
                pass
            await asyncio.sleep(0.5)
    finally:
        if own:
            await client.aclose()
    jobs[:] = [j for j in jobs if j.prefilter_reason != "posting no longer online"]
    return done
